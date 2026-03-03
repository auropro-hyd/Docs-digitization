const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export type WSMessage = {
  type: string;
  status?: string;
  total_pages?: number;
  pages_count?: number;
  pages?: number[];
  error?: string;
  [key: string]: unknown;
};

export type WSCallback = (message: WSMessage) => void;

export class DocumentWebSocket {
  private ws: WebSocket | null = null;
  private docId: string;
  private callbacks: Set<WSCallback> = new Set();
  private reconnectAttempts = 0;
  private maxReconnects = 5;

  constructor(docId: string) {
    this.docId = docId;
  }

  connect() {
    this.ws = new WebSocket(`${WS_BASE}/ws/${this.docId}`);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WSMessage;
        this.callbacks.forEach((cb) => cb(data));
      } catch {
        // ignore parse errors
      }
    };

    this.ws.onclose = () => {
      if (this.reconnectAttempts < this.maxReconnects) {
        this.reconnectAttempts++;
        setTimeout(() => this.connect(), 1000 * this.reconnectAttempts);
      }
    };
  }

  subscribe(callback: WSCallback) {
    this.callbacks.add(callback);
    return () => this.callbacks.delete(callback);
  }

  disconnect() {
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
