const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export type WSMessage = {
  type: string;
  status?: string;
  total_pages?: number;
  pages_count?: number;
  pages?: number[];
  error?: string;
  page_num?: number;
  confidence?: number;
  [key: string]: unknown;
};

export type WSCallback = (message: WSMessage) => void;

export class DocumentWebSocket {
  private ws: WebSocket | null = null;
  private docId: string;
  private callbacks: Set<WSCallback> = new Set();
  private reconnectAttempts = 0;
  private maxReconnects = 10;
  private intentionalClose = false;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(docId: string) {
    this.docId = docId;
  }

  connect() {
    if (this.intentionalClose) return;

    this.ws = new WebSocket(`${WS_BASE}/ws/${this.docId}`);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.startPing();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WSMessage;
        if (data.type === "pong") return;
        this.callbacks.forEach((cb) => cb(data));
      } catch {
        console.warn("[WS] Failed to parse message:", event.data);
      }
    };

    this.ws.onclose = () => {
      this.stopPing();
      if (this.intentionalClose) return;
      if (this.reconnectAttempts < this.maxReconnects) {
        this.reconnectAttempts++;
        const delay = Math.min(1000 * this.reconnectAttempts, 8000);
        setTimeout(() => this.connect(), delay);
      }
    };

    this.ws.onerror = () => {
      this.stopPing();
    };
  }

  private startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      this.send({ type: "ping" });
    }, 15_000);
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  subscribe(callback: WSCallback) {
    this.callbacks.add(callback);
    return () => this.callbacks.delete(callback);
  }

  disconnect() {
    this.intentionalClose = true;
    this.stopPing();
    this.ws?.close();
    this.ws = null;
    this.callbacks.clear();
  }

  send(data: object) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }
}
