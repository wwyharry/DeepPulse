'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export interface StreamEvent {
  type: 'session' | 'thinking' | 'content' | 'tool_call' | 'tool_result' | 'done' | 'error';
  delta?: string;
  name?: string;
  args?: Record<string, unknown>;
  data?: string;
  session_id?: string;
  rounds?: number;
  tools?: number;
  message?: string;
}

export function useChat() {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const sendMessage = useCallback((content: string, existingSessionId?: string) => {
    // 关闭旧连接
    if (wsRef.current) {
      wsRef.current.close();
    }

    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/chat`);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsStreaming(true);
      setEvents([]);
      ws.send(
        JSON.stringify({
          type: 'message',
          content,
          session_id: existingSessionId || sessionId || undefined,
        })
      );
    };

    ws.onmessage = (e) => {
      const event: StreamEvent = JSON.parse(e.data);
      setEvents((prev) => [...prev, event]);

      if (event.type === 'session' && event.session_id) {
        setSessionId(event.session_id);
      }

      if (event.type === 'done' || event.type === 'error') {
        setIsStreaming(false);
        ws.close();
      }
    };

    ws.onerror = () => {
      setIsStreaming(false);
    };

    ws.onclose = () => {
      setIsStreaming(false);
    };
  }, [sessionId]);

  const stop = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }));
    }
    setIsStreaming(false);
  }, []);

  const reset = useCallback(() => {
    setEvents([]);
    setSessionId(null);
  }, []);

  // 清理
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  return {
    events,
    isStreaming,
    sessionId,
    sendMessage,
    stop,
    reset,
  };
}
