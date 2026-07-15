'use client';

import { StreamEvent } from '@/hooks/useWebSocket';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolCallBadge } from './ToolCallBadge';

interface MessageBubbleProps {
  role: 'user' | 'assistant';
  content?: string;
  events?: StreamEvent[];
  isStreaming?: boolean;
}

export function MessageBubble({ role, content, events, isStreaming }: MessageBubbleProps) {
  if (role === 'user') {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[80%] px-4 py-2 bg-blue-600 text-white rounded-lg">
          {content}
        </div>
      </div>
    );
  }

  // Assistant 消息
  const thinking = events
    ?.filter((e) => e.type === 'thinking')
    .map((e) => e.delta || '')
    .join('') || '';

  const toolCalls = events?.filter((e) => e.type === 'tool_call') || [];
  const toolResults = events?.filter((e) => e.type === 'tool_result') || [];
  const contentText = events
    ?.filter((e) => e.type === 'content')
    .map((e) => e.delta || '')
    .join('') || content || '';

  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[85%] space-y-2">
        {/* 推理过程 */}
        {thinking && (
          <ThinkingBlock content={thinking} isStreaming={isStreaming || false} />
        )}

        {/* 工具调用 */}
        {toolCalls.map((tc, i) => {
          const result = toolResults[i];
          return (
            <ToolCallBadge
              key={i}
              name={tc.name || ''}
              args={tc.args}
              result={result?.data}
              status={result ? 'done' : 'calling'}
            />
          );
        })}

        {/* 正式内容 */}
        {contentText && (
          <div className="px-4 py-3 bg-[#1f2937] text-gray-200 rounded-lg whitespace-pre-wrap">
            {contentText}
            {isStreaming && (
              <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5" />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
