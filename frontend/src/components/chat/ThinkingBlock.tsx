'use client';

import { useState } from 'react';

interface ThinkingBlockProps {
  content: string;
  isStreaming: boolean;
}

export function ThinkingBlock({ content, isStreaming }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(true);

  if (!content) return null;

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800/50 mb-3">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 w-full p-3 text-sm text-gray-400 hover:text-gray-200 transition"
      >
        <span className="text-base">🧠</span>
        <span>推理过程</span>
        {isStreaming && (
          <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse ml-1" />
        )}
        <span className="ml-auto text-xs">
          {isOpen ? '▼' : '▶'}
        </span>
      </button>

      {isOpen && (
        <div className="px-3 pb-3 border-t border-gray-700/50">
          <div className="pl-4 border-l-2 border-blue-500/30 text-sm text-gray-400 leading-relaxed whitespace-pre-wrap mt-2">
            {content}
            {isStreaming && (
              <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
