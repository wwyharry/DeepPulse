'use client';

import { useEffect, useRef, useState } from 'react';
import { useChat, StreamEvent } from '@/hooks/useWebSocket';
import { MessageBubble } from './MessageBubble';

interface Message {
  role: 'user' | 'assistant';
  content?: string;
  events?: StreamEvent[];
}

export function ChatPanel() {
  const { events, isStreaming, sendMessage, stop } = useChat();
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events, messages]);

  // 当流式结束时，将 events 合入 messages
  useEffect(() => {
    if (!isStreaming && events.length > 0) {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === 'assistant' && last.events === events) {
          return prev;
        }
        return [...prev, { role: 'assistant', events: [...events] }];
      });
    }
  }, [isStreaming, events]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    setInput('');
    sendMessage(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-gray-500">
            <div className="text-center">
              <div className="text-4xl mb-4">🤖</div>
              <p className="text-lg">开始分析 A 股行情</p>
              <p className="text-sm mt-2">输入股票名称或代码，例如：分析一下贵州茅台</p>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            role={msg.role}
            content={msg.content}
            events={msg.events}
          />
        ))}

        {/* 当前流式输出 */}
        {isStreaming && (
          <MessageBubble
            role="assistant"
            events={events}
            isStreaming={true}
          />
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* 输入框 */}
      <div className="border-t border-gray-800 p-4">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
            className="flex-1 px-4 py-3 bg-[#1f2937] border border-gray-700 rounded-lg text-gray-200 placeholder-gray-500 resize-none focus:outline-none focus:border-blue-500 transition"
            rows={1}
            disabled={isStreaming}
          />
          {isStreaming ? (
            <button
              onClick={stop}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg transition"
            >
              停止
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg transition"
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
