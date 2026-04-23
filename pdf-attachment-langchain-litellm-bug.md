# PDF attachments: `KeyError: 'file'` from Anthropic‑via‑Vertex with LangChain v1 + LiteLLM

**Status.** Workaround shipped in our repo (route document‑bearing turns to Gemini via `generation.document_fallback_model`). Upstream fix still needed in three projects. This document is a self‑contained technical RCA + patch plan for opening those issues / PRs.

**Affected stack (pinned exactly).**

| Package | Version |
|---|---|
| `litellm` | **1.83.8** (bug also present in latest stable `1.83.11` and nightly `v1.83.10-nightly`) |
| `langchain-core` | **1.2.7** |
| `langchain` | 1.2.6 |
| `langchain-litellm` | **0.3.5** |
| `langgraph` | 1.0.6 |
| `pydantic` | 2.12.5 |
| Python | 3.13.7 |

**Scope of impact.** Anyone on `langchain-core >= 1.0` + `langchain-litellm` + a LiteLLM Anthropic route (direct Anthropic *or* Anthropic‑via‑Vertex `vertex_ai/claude-*`) who attaches a PDF to a `HumanMessage` using the **OpenAI Chat Completions file block** — the exact shape the [OpenAI docs prescribe](https://platform.openai.com/docs/api-reference/chat/create) and the shape LiteLLM itself documents for PDFs:

```python
{"type": "file", "file": {"file_data": "data:application/pdf;base64,...", "format": "application/pdf"}}
```

Every such call throws `litellm.exceptions.InternalServerError: Vertex_aiException InternalServerError - 'file'` (Anthropic‑via‑Vertex) or the equivalent on direct Anthropic — **before the LLM is ever contacted**. Failure is deterministic, not intermittent.

This is a three‑way interaction bug. Each layer behaves defensibly in isolation, but together they break. Upstream fixes are needed in all three; the most important one is **LiteLLM** (one‑line defensive patch on a public API invariant).

---

## Table of contents

1. [Symptom](#symptom)
2. [Minimal reproduction (~40 lines, no network)](#minimal-reproduction)
3. [Full call chain — byte‑level](#full-call-chain--bytelevel)
4. [Root cause breakdown per package](#root-cause-breakdown-per-package)
5. [Suggested fixes per package](#suggested-fixes-per-package)
6. [Why `client‑side workaround` is not the right answer](#why-clientside-workaround-is-not-the-right-answer)
7. [Test plan for the upstream patches](#test-plan-for-the-upstream-patches)
8. [Related code references](#related-code-references)
9. [Appendix A — full production traceback](#appendix-a--full-production-traceback)
10. [Appendix B — our local workaround](#appendix-b--our-local-workaround)

---

## Symptom

Client (our agent, but reproduces with plain `ChatLiteLLM` — see §2):

```python
await chat_model.ainvoke([
    HumanMessage(content=[
        {"type": "text", "text": "summarise this PDF"},
        {"type": "file", "file": {
            "file_data": "data:application/pdf;base64,JVBERi0xLjQK...",
            "format": "application/pdf",
        }},
    ]),
])
```

Crash (abbreviated — [full trace in Appendix A](#appendix-a--full-production-traceback)):

```text
File ".../litellm/llms/anthropic/chat/handler.py", line 351, in completion
    headers = AnthropicConfig().validate_environment(...)
File ".../litellm/llms/anthropic/common_utils.py", line 508, in validate_environment
    file_id_used = self.is_file_id_used(messages=messages)
File ".../litellm/llms/anthropic/common_utils.py", line 113, in is_file_id_used
    file_ids = get_file_ids_from_messages(messages)
File ".../litellm/litellm_core_utils/prompt_templates/common_utils.py", line 1063,
    in get_file_ids_from_messages
    file_object_file_field = file_object["file"]
                             ~~~~~~~~~~~^^^^^^^^
KeyError: 'file'

During handling of the above exception, another exception occurred:
...
litellm.llms.vertex_ai.vertex_ai_partner_models.main.VertexAIError: 'file'
litellm.exceptions.InternalServerError: Vertex_aiException InternalServerError - 'file'
```

## Minimal reproduction

No Vertex credentials needed — the crash fires **before** any network call, in `AnthropicConfig.validate_environment`.

```python
# repro.py — crashes on:
#   pip install 'langchain-core>=1.0' 'langchain-litellm>=0.3' 'litellm>=1.83'
import asyncio, base64
from langchain_core.messages import HumanMessage
from langchain_litellm import ChatLiteLLM

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"

msg = HumanMessage(content=[
    {"type": "text", "text": "what is this?"},
    {"type": "file", "file": {
        "file_data": f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}",
        "format": "application/pdf",
    }},
])

cm = ChatLiteLLM(model="vertex_ai/claude-haiku-4-5", max_tokens=10, streaming=False)
# Same crash on direct Anthropic: model="anthropic/claude-haiku-4-5"

async def go() -> None:
    try:
        await cm.ainvoke([msg])
    except Exception as e:
        print(f"{type(e).__name__}: {e}")

asyncio.run(go())
# → InternalServerError: litellm.InternalServerError:
#   Vertex_aiException InternalServerError - 'file'
```

The crash is 100% deterministic on the versions listed above. You can confirm without dependencies by running the instrumentation block below:

```python
# confirm which stage mutates the content
from langchain_litellm.chat_models import litellm as _llm_mod
_orig = _llm_mod._convert_message_to_dict

def _wrap(m):
    for p in (m.content if isinstance(m.content, list) else []):
        if isinstance(p, dict) and p.get("type") == "file":
            print("  BEFORE _convert_message_to_dict:", sorted(p.keys()))
    r = _orig(m)
    for p in (r["content"] if isinstance(r.get("content"), list) else []):
        if isinstance(p, dict) and p.get("type") == "file":
            print("  AFTER  _convert_message_to_dict:", sorted(p.keys()))
    return r

_llm_mod._convert_message_to_dict = _wrap
```

Output with the minimal repro above:

```text
BEFORE _convert_message_to_dict: ['base64', 'extras', 'id', 'mime_type', 'type']
AFTER  _convert_message_to_dict: ['base64', 'extras', 'id', 'mime_type', 'type']
```

The construction of `HumanMessage` kept our OpenAI shape (`['file', 'type']`), but by the time `ChatLiteLLM._astream` invokes `_convert_message_to_dict`, the content block has already been rewritten to `langchain-core`'s **v1 "standardized content block"** shape — `['base64', 'extras', 'id', 'mime_type', 'type']` — **with no `file` key**. LiteLLM's Anthropic handler then crashes on `c["file"]`.

## Full call chain — byte‑level

```text
 Caller                                                   Content block shape
 ──────                                                   ──────────────────
 1. HumanMessage(content=[..., {type:"file",file:{...}}]) {type, file}            ← OpenAI shape

 2. ChatLiteLLM.ainvoke([msg])
      → BaseChatModel.ainvoke
      → BaseChatModel._agenerate → _agenerate_with_cache
      → BaseChatModel._astream
                                                          {type, file}

 3. langchain_core.language_models.chat_models._astream
    line 663: input_messages = _normalize_messages(messages)       ← MUTATION HAPPENS HERE

 4. langchain_core.language_models._utils._normalize_messages
    line 251-260: for each block where
        block["type"] in {"input_audio","file"} AND is_openai_data_block(block):
          converted = _convert_openai_format_to_data_block(block)
          _update_content_block(formatted_message, idx, converted)

 5. langchain_core.messages.block_translators.openai._convert_openai_format_to_data_block
    line 534-553 ("base64-style file block"):
      base64_data = parsed["data"]              # from data: URI
      file_extras = {"format": "application/pdf"} → prefixed → "file_format"
      return types.create_file_block(
          base64=base64_data,
          mime_type="application/pdf",
          filename=None,
          file_format="application/pdf",        # prefixed extras
      )                                          → {type:"file", id:"lc_…",
                                                     base64:…, mime_type:…,
                                                     extras:{file_format:…}}

 6. langchain_litellm.chat_models.litellm._astream
    line 551: message_dicts, params = self._create_message_dicts(messages, stop)
    line 499:   [_convert_message_to_dict(m) for m in messages]
    line 238-266: _convert_message_to_dict(m)
        message_dict = {"content": message.content, "role": "user", …}
      # ← NO outbound v1 → OpenAI translation; the v1 shape leaks through

                                                          {type:"file", id, base64, mime_type, extras}

 7. litellm.acompletion → litellm.completion → provider dispatch
    vertex_ai_partner_models.main.completion (Claude branch at line 198-218)
      → anthropic_chat_completions.completion
 8. litellm.llms.anthropic.chat.handler.completion
    line 350: messages = copy.deepcopy(messages)          # unchanged
    line 351: headers = AnthropicConfig().validate_environment(messages=messages, …)

 9. litellm.llms.anthropic.common_utils.validate_environment
    line 505: pdf_used = self.is_pdf_used(messages=messages)        ← works, just checks non-text
    line 506: file_id_used = self.is_file_id_used(messages=messages)← CRASH

10. litellm.llms.anthropic.common_utils.is_file_id_used
    line 113: file_ids = get_file_ids_from_messages(messages)

11. litellm.litellm_core_utils.prompt_templates.common_utils.get_file_ids_from_messages
    line 1061: for c in content:
    line 1062:     if c["type"] == "file":             ← matches (type IS "file")
    line 1063:         file_object_file_field = file_object["file"]   ← KeyError: 'file'
```

## Root cause breakdown per package

### LiteLLM — unsafe dict access on a public content‑block invariant

**File.** [`litellm/litellm_core_utils/prompt_templates/common_utils.py`](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/prompt_templates/common_utils.py) — lines **1049–1068** on `v1.83.8`; identical on `v1.83.10-nightly` / `1.83.11`.

```python
def get_file_ids_from_messages(messages: List[AllMessageValues]) -> List[str]:
    file_ids = []
    for message in messages:
        if message.get("role") == "user":
            content = message.get("content")
            if content:
                if isinstance(content, str):
                    continue
                for c in content:
                    if c["type"] == "file":
                        file_object = cast(ChatCompletionFileObject, c)
                        file_object_file_field = file_object["file"]   # ← line 1063: unsafe
                        file_id = file_object_file_field.get("file_id")
                        if file_id:
                            file_ids.append(file_id)
    return file_ids
```

The function short‑circuits on `c["type"] == "file"` and then assumes `c["file"]` exists. The comment on [`AnthropicConfig.is_file_id_used`](https://github.com/BerriAI/litellm/blob/main/litellm/llms/anthropic/common_utils.py) (line 110) documents its intent as *"Return if `{"source": {"type": "file", "file_id": ..}}` is in message content block"* — i.e. it is looking specifically for OpenAI Chat Completions file blocks. Any other block whose `type` string happens to be `"file"` causes an unhandled `KeyError`, which the Vertex partner layer wraps as an `InternalServerError`.

A second copy of the same unsafe pattern is at **line 455** of the same file, inside [`update_messages_with_model_file_ids`](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/prompt_templates/common_utils.py#L445) — same class of bug, different call site (model‑scoped file‑id remapping).

> **Severity.** High. Any consumer passing a `type:"file"` block that isn't exactly in OpenAI shape — whether from LangChain v1, a LiteLLM‑callable provider that emits file‑ish blocks, or a user constructing their own content — will crash in `validate_environment`, before any request goes out. The Anthropic handler calls `is_file_id_used` unconditionally on every request, so this is **always** reachable for any request with a user‑content block whose `type == "file"`.

### langchain‑core — `_normalize_messages` rewrites OpenAI input to v1 without a corresponding outbound hook

**File.** [`langchain_core/language_models/_utils.py`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/language_models/_utils.py) — `_normalize_messages` defined at line **124**, called from [`BaseChatModel._astream`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/language_models/chat_models.py) at line **663** (and from `_stream` at 535, `batch`/`abatch` at 925).

The rewriting step is lines 248–287 of `_utils.py`:

```python
if isinstance(message.content, list):
    for idx, block in enumerate(message.content):
        if (
            isinstance(block, dict)
            and block.get("type") in {"input_audio", "file"}
            and is_openai_data_block(block)                # ← discriminator
        ):
            formatted_message = _ensure_message_copy(message, formatted_message)
            converted_block = _convert_openai_format_to_data_block(block)
            _update_content_block(formatted_message, idx, converted_block)
        # … v0‑to‑v1 branch elided
        # else, pass through blocks that look like they have v1 format unchanged
```

The docstring (line 127) commits to "Normalize message formats to LangChain v1 standard content blocks" and the 1.0 changelog explicitly says: *"In previous versions, this function returned messages in LangChain v0 format. Now, it returns messages in LangChain v1 format, which upgraded chat models now expect to receive when passing back in message history."*

**This is reasonable in isolation** — v1 is LangChain's new internal currency. The problem is the asymmetry: `_normalize_messages` is called *on the way in* to every chat model, but there is **no corresponding outbound translator** that converts v1 back to whatever shape the concrete chat‑model backend expects. langchain‑core leaves that to each chat model's integration. For OpenAI/Anthropic/Google native integrations that's fine — they know about v1 and translate it themselves. For `langchain-litellm`, which is a thin pass‑through to a multi‑provider router, it is not fine: `langchain-litellm` has no idea about LangChain v1 blocks.

**Interior note, `_convert_openai_format_to_data_block`.** The translator at [`messages/block_translators/openai.py`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/block_translators/openai.py) line **534–553** (base64‑file branch) hard‑codes `mime_type="application/pdf"`:

```python
# base64-style file block
if (block["type"] == "file") and (
    parsed := _parse_data_uri(block["file"]["file_data"])
):
    …
    filename = block["file"].get("filename")
    return types.create_file_block(
        base64=parsed["data"],
        mime_type="application/pdf",              # ← hard-coded
        filename=filename,
        **all_extras,
    )
```

The parsed data URI's `mime_type` is discarded — every file that comes in base64 becomes `application/pdf`. That's a second (lower‑severity) bug: non‑PDF files (e.g. audio encoded as `data:audio/ogg;base64,…`) would be mislabelled if they survived this far.

### langchain‑litellm — no outbound v1 → OpenAI translation before handing off to LiteLLM

**File.** [`langchain_litellm/chat_models/litellm.py`](https://github.com/Akshay-Dongare/langchain-litellm/blob/main/langchain_litellm/chat_models/litellm.py) line **238**:

```python
def _convert_message_to_dict(message: BaseMessage) -> dict:
    message_dict: Dict[str, Any] = {"content": message.content}     # ← verbatim
    …
```

`_convert_message_to_dict` sets `content` to `message.content` verbatim. Upstream `_normalize_messages` has already rewritten that list from OpenAI shape to v1 shape; `langchain-litellm` forwards the v1 shape to LiteLLM, which only understands OpenAI shape.

For the `AIMessage` / chunk direction, the translator pair does exist (`_convert_delta_to_message_chunk` etc.) — but the **content‑block outbound direction** is missing. It should call [`convert_to_openai_data_block`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/block_translators/openai.py#L57) (also exposed under `langchain_core.messages.block_translators.openai`) on every list‑typed content block that matches a v1 standardized type.

## Suggested fixes per package

### Priority 1 — LiteLLM (patch the unsafe access)

Two one‑line defensive changes. Both functions exist in `litellm/litellm_core_utils/prompt_templates/common_utils.py`.

**1a.** `get_file_ids_from_messages` — line 1063:

```diff
 for c in content:
     if c["type"] == "file":
-        file_object = cast(ChatCompletionFileObject, c)
-        file_object_file_field = file_object["file"]
-        file_id = file_object_file_field.get("file_id")
+        file_object_file_field = c.get("file")
+        if not isinstance(file_object_file_field, dict):
+            continue  # `type:"file"` but not the OpenAI Chat Completions shape
+        file_id = file_object_file_field.get("file_id")
         if file_id:
             file_ids.append(file_id)
```

**1b.** `update_messages_with_model_file_ids` — same file, around line 455, identical treatment:

```diff
 for c in content:
     if c["type"] == "file":
-        file_object = cast(ChatCompletionFileObject, c)
-        file_object_file_field = file_object["file"]
+        file_object_file_field = c.get("file")
+        if not isinstance(file_object_file_field, dict):
+            continue
         file_id = file_object_file_field.get("file_id")
         format = file_object_file_field.get(
             "format", get_format_from_file_id(file_id)
         )
         …
```

**Why this is necessary even if the other two fix their side.** `type == "file"` is a public content‑block discriminator that *any* caller can produce. The LangChain v1 shape is one non‑OpenAI producer among several (LangChain v0 SDKs, custom user code, future providers). LiteLLM should never crash with `KeyError` on a well‑typed content list just because a `"file"` block doesn't match its expected OpenAI sub‑shape.

**File/reference for PR.**  
Repo: `https://github.com/BerriAI/litellm`  
File: `litellm/litellm_core_utils/prompt_templates/common_utils.py`  
Add regression test in `tests/local_testing/test_prompt_factory.py` or wherever similar content‑block tests live:

```python
def test_get_file_ids_from_messages_tolerates_non_openai_file_blocks():
    # LangChain v1 content-block shape (no `file` sub-key)
    messages = [
        {"role": "user", "content": [
            {"type": "file", "id": "lc_x", "base64": "AAAA",
             "mime_type": "application/pdf", "extras": {}},
        ]}
    ]
    # Must not KeyError
    assert get_file_ids_from_messages(messages) == []
```

### Priority 2 — langchain‑litellm (apply the outbound translation)

Make `_convert_message_to_dict` convert v1 standardized content blocks back to OpenAI Chat Completions format before handing off to LiteLLM. LiteLLM expects OpenAI shape — that is its input contract.

```diff
 def _convert_message_to_dict(message: BaseMessage) -> dict:
-    message_dict: Dict[str, Any] = {"content": message.content}
+    message_dict: Dict[str, Any] = {"content": _to_openai_content(message.content)}
     …

+from langchain_core.messages.block_translators.openai import (
+    convert_to_openai_data_block,
+)
+
+_V1_DATA_BLOCK_TYPES = {"image", "audio", "file"}
+
+def _to_openai_content(content):
+    """Convert LangChain v1 standardized data blocks to OpenAI Chat Completions
+    shape before forwarding to LiteLLM. Other block types pass through.
+    """
+    if not isinstance(content, list):
+        return content
+    converted = []
+    for block in content:
+        if (
+            isinstance(block, dict)
+            and block.get("type") in _V1_DATA_BLOCK_TYPES
+            and (
+                "base64" in block           # v1 base64 block
+                or "url" in block           # v1 URL block
+                or "file_id" in block       # v1 file-id block
+                or "source_type" in block   # v0 shape that also leaks through
+            )
+        ):
+            converted.append(convert_to_openai_data_block(block))
+        else:
+            converted.append(block)
+    return converted
```

This is the symmetric counterpart to the `_normalize_messages` call on the way in. With both in place, the round trip *OpenAI → v1 → OpenAI* is lossless and LiteLLM sees the shape it documents.

**File/reference for PR.**  
Repo: `https://github.com/Akshay-Dongare/langchain-litellm` (main branch lives here; `langchain-litellm` is the package name)  
File: `langchain_litellm/chat_models/litellm.py`  
Regression test: round‑trip `HumanMessage(content=[{"type":"file","file":{"file_data":"data:…","format":"application/pdf"}}])` through `_convert_message_to_dict` *after* `_normalize_messages` has been applied; assert the output has `type:"file"` with a `"file"` sub‑dict that contains `file_data`.

### Priority 3 — langchain‑core (lower‑severity correctness fix)

Fix the hard‑coded `mime_type="application/pdf"` in `_convert_openai_format_to_data_block` so non‑PDF base64 file blocks keep their MIME type. Not related to the prod crash, but a correctness bug caught in passing while reading the code.

```diff
 # base64-style file block
 if (block["type"] == "file") and (
     parsed := _parse_data_uri(block["file"]["file_data"])
 ):
     …
     filename = block["file"].get("filename")
     return types.create_file_block(
         base64=parsed["data"],
-        mime_type="application/pdf",
+        mime_type=parsed["mime_type"],
         filename=filename,
         **all_extras,
     )
```

**File/reference for PR.**  
Repo: `https://github.com/langchain-ai/langchain`  
File: `libs/core/langchain_core/messages/block_translators/openai.py`

## Why `client‑side workaround` is not the right answer

For completeness, a few alternatives we considered:

- **Emit v1 directly** (`{"type":"file","base64":…,"mime_type":…}`) — works (skips `_normalize_messages`), but we'd be hand‑emitting LangChain internal shape from application code. Breaks the day v1 moves.
- **Emit Anthropic‑native `{"type":"document","source":{...}}`** — works (LangChain doesn't recognise it as a standardized block, so it passes through; LiteLLM's Anthropic user‑content transformer accepts `document` blocks directly). But it's provider‑specific and pushes us to branch on model ID.
- **Pin `langchain-core<1.0`** — locks us out of every langchain update for the lifetime of the bug.
- **Monkey‑patch LiteLLM's `get_file_ids_from_messages`** — works, but silently patches a library from application code, which is hostile to other consumers in the same process.

**We shipped a different workaround**: detect `state.documents != []` at the node level and swap in Gemini 2.5 Pro for that single turn. Gemini accepts `application/pdf` natively via `image_url`, so the buggy Anthropic `is_file_id_used` path is never invoked. See [Appendix B](#appendix-b--our-local-workaround) for the patch. That's orthogonal to fixing the upstream bug — which is still needed, because (a) Anthropic should be a first‑class provider for attachments, not a fallback, and (b) this bug will keep biting every other consumer until one of the three layers is patched.

## Test plan for the upstream patches

A single regression test against LiteLLM's `get_file_ids_from_messages` is enough to prove the primary fix. The full end‑to‑end test that proves the interaction is the repro from §2.

```python
# Integration-level regression: runs against a mocked LiteLLM transport.
import asyncio, base64
from langchain_core.messages import HumanMessage
from langchain_litellm import ChatLiteLLM
from unittest.mock import patch

PDF = b"%PDF-1.4\n%%EOF"
msg = HumanMessage(content=[
    {"type": "file", "file": {
        "file_data": f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}",
        "format": "application/pdf",
    }},
])

async def run() -> None:
    cm = ChatLiteLLM(model="anthropic/claude-haiku-4-5", max_tokens=10, streaming=False)
    # With any of the three fixes applied, the pipeline must reach the mocked transport.
    with patch("litellm.llms.anthropic.chat.handler.AnthropicChatCompletion.completion") as mock:
        mock.return_value = ...  # whatever the stub returns
        await cm.ainvoke([msg])
    args, kwargs = mock.call_args
    # The block arriving at the transport must be the OpenAI shape:
    content = kwargs["messages"][0]["content"]
    file_block = next(p for p in content if p.get("type") == "file")
    assert "file" in file_block                          # the crash condition
    assert file_block["file"]["file_data"].startswith("data:application/pdf;base64,")

asyncio.run(run())
```

## Related code references

LiteLLM (all paths relative to `litellm/`):
- [`llms/anthropic/chat/handler.py:351`](https://github.com/BerriAI/litellm/blob/main/litellm/llms/anthropic/chat/handler.py) — `AnthropicConfig().validate_environment(messages=messages, …)` on every Anthropic completion.
- [`llms/anthropic/common_utils.py:474–512`](https://github.com/BerriAI/litellm/blob/main/litellm/llms/anthropic/common_utils.py) — `validate_environment`, which calls `is_file_id_used` on line 506.
- [`llms/anthropic/common_utils.py:109–114`](https://github.com/BerriAI/litellm/blob/main/litellm/llms/anthropic/common_utils.py) — `is_file_id_used`.
- [`litellm_core_utils/prompt_templates/common_utils.py:1049–1068`](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/prompt_templates/common_utils.py) — `get_file_ids_from_messages` (**crash site**, line 1063).
- [`litellm_core_utils/prompt_templates/common_utils.py:445–469`](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/prompt_templates/common_utils.py) — `update_messages_with_model_file_ids` (second unsafe access, line 455).
- [`llms/vertex_ai/vertex_ai_partner_models/main.py:198–218`](https://github.com/BerriAI/litellm/blob/main/litellm/llms/vertex_ai/vertex_ai_partner_models/main.py) — Vertex‑partner dispatch that wraps the inner `KeyError` as `VertexAIError(status_code=500, message=str(e))`, which LiteLLM's exception mapper turns into `InternalServerError`.

langchain‑core:
- [`language_models/chat_models.py:663`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/language_models/chat_models.py) — `_astream` invokes `_normalize_messages(messages)`.
- [`language_models/_utils.py:124–289`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/language_models/_utils.py) — `_normalize_messages`.
- [`messages/block_translators/openai.py:426–556`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/block_translators/openai.py) — `_convert_openai_format_to_data_block`.
- [`messages/block_translators/openai.py:57–149`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/block_translators/openai.py) — `convert_to_openai_data_block` (already exists; currently unused in the outbound path for langchain‑litellm).
- [`messages/base.py:199–261`](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/base.py) — `BaseMessage.content_blocks` property (separate on‑demand normaliser).

langchain‑litellm:
- [`chat_models/litellm.py:238–266`](https://github.com/Akshay-Dongare/langchain-litellm/blob/main/langchain_litellm/chat_models/litellm.py) — `_convert_message_to_dict` (no outbound translation).
- [`chat_models/litellm.py:491–500`](https://github.com/Akshay-Dongare/langchain-litellm/blob/main/langchain_litellm/chat_models/litellm.py) — `_create_message_dicts`, calls `_convert_message_to_dict` per message.
- [`chat_models/litellm.py:544–590`](https://github.com/Akshay-Dongare/langchain-litellm/blob/main/langchain_litellm/chat_models/litellm.py) — `_astream`, the crash site call chain.

## Appendix A — full production traceback

Captured in dev on pod `retrieval-agents-7b895b9cd6-pjvds`, 2026‑04‑22 04:37:33 UTC, correlation `0159712b-1769-4ba2-bc3b-c5f5ba6d085b`, thread `6279ec2e-5a8d-45f0-bc37-fedb177ade5c`:

```text
Traceback (most recent call last):
  File ".../litellm/llms/vertex_ai/vertex_ai_partner_models/main.py", line 210, in completion
    return anthropic_chat_completions.completion(
        model=model,
        …
        custom_llm_provider=LlmProviders.VERTEX_AI.value,
    )
  File ".../litellm/llms/anthropic/chat/handler.py", line 351, in completion
    headers = AnthropicConfig().validate_environment(
        api_key=api_key,
        …
        litellm_params=litellm_params,
    )
  File ".../litellm/llms/anthropic/common_utils.py", line 508, in validate_environment
    file_id_used = self.is_file_id_used(messages=messages)
  File ".../litellm/llms/anthropic/common_utils.py", line 113, in is_file_id_used
    file_ids = get_file_ids_from_messages(messages)
  File ".../litellm/litellm_core_utils/prompt_templates/common_utils.py", line 1063,
      in get_file_ids_from_messages
    file_object_file_field = file_object["file"]
                             ~~~~~~~~~~~^^^^^^^^
KeyError: 'file'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File ".../litellm/main.py", line 3491, in completion
    model_response = vertex_partner_models_chat_completion.completion(
        model=model,
        …
        client=client,
    )
  File ".../litellm/llms/vertex_ai/vertex_ai_partner_models/main.py", line 270, in completion
    raise VertexAIError(status_code=500, message=str(e))
litellm.llms.vertex_ai.vertex_ai_partner_models.main.VertexAIError: 'file'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  …
  File ".../langchain_core/language_models/chat_models.py", line 1316,
      in _agenerate_with_cache
    async for chunk in self._astream(messages, stop=stop, **kwargs):
  File ".../langchain_litellm/chat_models/litellm.py", line 555, in _astream
    async for chunk in await self.acompletion_with_retry(
        messages=message_dicts, run_manager=run_manager, **params
    ):
  …
  File ".../litellm/main.py", line 4411, in completion
    raise exception_type(…)
  File ".../litellm/litellm_core_utils/exception_mapping_utils.py", line 1482, in exception_type
    raise litellm.InternalServerError(
litellm.exceptions.InternalServerError:
    litellm.InternalServerError: Vertex_aiException InternalServerError - 'file'
During task with name 'agent' and id 'c88d0e3a-52cc-463c-c80f-ee2fee0d14f6'
```

## Appendix B — our local workaround

(For reference only — this is the change *we* shipped; upstream fixes are still needed.)

```yaml
# configs/generation.yaml
generation:
  agent_model: "vertex_ai/claude-sonnet-4-6"
  direct_response_model: "vertex_ai/claude-haiku-4-5"
  document_fallback_model: "vertex_ai/gemini-2.5-pro"   # swap-in for turns with attachments
```

```python
# src/.../graph/nodes/agent.py (and the twin in direct_response.py)
has_documents = bool(state.get("documents"))
per_request_override = config.get("configurable", {}).get("model_override")
doc_fallback = gen_settings.document_fallback_model if has_documents else ""
effective_model = (
    per_request_override
    or doc_fallback
    or gen_settings.agent_model
    or gen_settings.model
)
```

PDF‑bearing turns are pinned to Gemini for the duration of the turn. Anthropic's `is_file_id_used` is never reached on those turns, so the KeyError is silenced at the provider level. Non‑attachment turns remain on the user‑selected agent model.

---

*Document owner:* the person opening the upstream PRs. Start with **LiteLLM** (one‑line defensive patch, highest leverage), then **langchain‑litellm** (correctness, closes the loop), then **langchain‑core** (drive‑by correctness on the hard‑coded mime type). Reference this document in the issue bodies.
