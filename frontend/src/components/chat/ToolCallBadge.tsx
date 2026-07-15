'use client';

import { useState } from 'react';

interface ToolCallBadgeProps {
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  status: 'calling' | 'done';
}

export function ToolCallBadge({ name, args, result, status }: ToolCallBadgeProps) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="mb-2">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-md text-sm transition"
      >
        <span className={status === 'calling' ? 'text-yellow-400' : 'text-green-400'}>
          {status === 'calling' ? '⏳' : '✅'}
        </span>
        <span className="text-gray-300 font-mono text-xs">{name}</span>
        {args && (
          <span className="text-gray-500 text-xs truncate max-w-[200px]">
            {JSON.stringify(args)}
          </span>
        )}
      </button>

      {isOpen && result && (
        <div className="mt-1 p-3 bg-gray-900 rounded-md text-xs font-mono text-gray-400 overflow-auto max-h-48">
          <pre className="whitespace-pre-wrap">{result}</pre>
        </div>
      )}
    </div>
  );
}
