/**
 * WebChatPage — vbit Agent 聊天界面（构建成功版）
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Send, Square, Plus, Trash2, Bot, User, Copy, Check, Paperclip, X, ChevronDown } from "lucide-react";

// ============ 类型 ============
interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  _images?: string[];
}

interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

interface AttachmentPreview {
  id: string;
  name: string;
  type: "image" | "file";
  mime: string;
  size: number;
  data: string;
  thumbnail?: string;
}

// ============ 常量 ============
const STORAGE_KEY = "vbit-agent-conversations";
const ACTIVE_KEY = "vbit-agent-active-conversation";
const MODEL_KEY = "vbit-agent-model";

const MAX_ATTACHMENTS = 5;
const MAX_FILE_SIZE = 2 * 1024 * 1024;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
const TEXT_TYPES = [
  "text/plain", "text/csv", "application/json", "text/markdown",
  "text/javascript", "text/typescript", "text/html", "text/css",
  "application/pdf", "application/x-python", "text/x-python",
  "text/x-sh", "application/xml", "text/xml",
];
const TEXT_EXTENSIONS = [
  ".txt", ".csv", ".json", ".md", ".py", ".js", ".ts",
  ".html", ".css", ".xml", ".yaml", ".yml", ".toml", ".sh", ".bat", ".log",
];

// ============ 模型列表 ============
const MODELS = [
  { value: "deepseek-chat", label: "DeepSeek V3" },
  { value: "deepseek-reasoner", label: "DeepSeek R1" },
  { value: "openrouter/owl-alpha", label: "Owl Alpha" },
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "claude-opus-4", label: "Claude Opus 4" },
];

const MODEL_DISPLAY: Record<string, string> = {};
MODELS.forEach(m => { MODEL_DISPLAY[m.value] = m.label; });

// ============ Markdown 渲染（纯 div 方案，无动态 JSX 标签）============
function renderMarkdown(content: string): React.ReactNode[] {
  const elements: React.ReactNode[] = [];
  const lines = content.split("\n");
  let inCodeBlock = false;
  let codeBuffer = "";
  let inList: null | "ul" | "ol" = null;

  const flushCodeBlock = () => {
    if (inCodeBlock && codeBuffer) {
      elements.push(
        React.createElement("pre", {
          key: `cb-${elements.length}`,
          className: "bg-black/30 rounded-lg p-3 my-2 overflow-x-auto text-sm",
          children: React.createElement("code", {}, codeBuffer.replace(/\n$/, ""))
        })
      );
      codeBuffer = "";
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block
    if (line.startsWith("```")) {
      if (inList) { inList = null; }
      if (!inCodeBlock) {
        inCodeBlock = true;
        continue;
      } else {
        inCodeBlock = false;
        flushCodeBlock();
        continue;
      }
    }
    if (inCodeBlock) {
      codeBuffer += line + "\n";
      continue;
    }

    // Headings — 用 div + className 代替动态 h1~h5
    const hMatch = line.match(/^(#{1,5})\s+(.+)$/);
    if (hMatch) {
      if (inList) { inList = null; }
      const level = hMatch[1].length;
      const text = hMatch[2];
      const sizeClass = level <= 2 ? "text-base font-bold" : "text-sm font-semibold";
      elements.push(
        React.createElement("div", {
          key: i,
          className: `${sizeClass} text-[#00d47e] mt-3 mb-1`,
          children: text
        })
      );
      continue;
    }

    // Unordered list
    const ulMatch = line.match(/^[-*]\s+(.+)$/);
    if (ulMatch) {
      if (inList !== "ul") { if (inList) { } inList = "ul"; }
      elements.push(
        React.createElement("div", { key: i, className: "pl-4 py-0.5 text-sm" }, [
          React.createElement("span", { key: "0", className: "text-[#00d47e]" }, "• "),
          ulMatch[1]
        ])
      );
      continue;
    }

    // Ordered list
    const olMatch = line.match(/^\d+\.\s+(.+)$/);
    if (olMatch) {
      if (inList !== "ol") { if (inList) { } inList = "ol"; }
      elements.push(
        React.createElement("div", { key: i, className: "pl-4 py-0.5 text-sm" },
          `${i + 1}. ${olMatch[1]}`
        )
      );
      continue;
    }

    if (inList) { inList = null; }

    // Horizontal rule
    if (line.match(/^---+$|^\*\*\*+$/)) {
      elements.push(React.createElement("hr", { key: i, className: "border-gray-700 my-3" }));
      continue;
    }

    // Blockquote
    if (line.startsWith("> ") || line === ">") {
      elements.push(
        React.createElement("blockquote", {
          key: i,
          className: "border-l-4 border-[#00d47e] pl-3 my-2 text-gray-400 italic",
          children: line.replace(/^>\s?/, "")
        })
      );
      continue;
    }

    // Empty line
    if (line.trim() === "") {
      elements.push(React.createElement("div", { key: i, className: "h-2" }));
      continue;
    }

    // Normal paragraph — inline formatting
    const parts = line.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g);
    const children = parts.map((part: string, j: number) => {
      if (part.startsWith("`") && part.endsWith("`")) {
        return React.createElement("code", { key: j, className: "bg-black/30 px-1.5 py-0.5 rounded text-xs" }, part.slice(1, -1));
      }
      if (part.startsWith("**") && part.endsWith("**")) {
        return React.createElement("strong", { key: j, className: "text-gray-100" }, part.slice(2, -2));
      }
      const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        return React.createElement("a", {
          key: j, href: linkMatch[2], target: "_blank", rel: "noopener",
          className: "text-[#00d47e] underline"
        }, linkMatch[1]);
      }
      return part;
    });
    elements.push(React.createElement("p", { key: i, className: "py-0.5 leading-relaxed text-sm" }, children));
  }

  flushCodeBlock();
  return elements;
}

// ============ 工具函数 ============
function generateId(): string {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveConversations(convs: Conversation[]): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(convs)); } catch (e) { console.error(e); }
}

function loadActiveId(): string | null {
  return localStorage.getItem(ACTIVE_KEY);
}

function saveActiveId(id: string | null): void {
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
}

function loadModel(): string {
  try {
    const m = localStorage.getItem(MODEL_KEY);
    return m && MODELS.some(x => x.value === m) ? m : MODELS[0].value;
  } catch { return MODELS[0].value; }
}

function extractTitle(msg: string): string {
  const clean = msg.replace(/\n/g, " ").trim();
  return clean.length > 30 ? clean.slice(0, 30) + "…" : clean;
}

// ============ 主组件 ============
export default function WebChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(loadActiveId);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [abortCtrl, setAbortCtrl] = useState<AbortController | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<AttachmentPreview[]>([]);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [currentModel, setCurrentModel] = useState<string>(loadModel);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);

  const activeConv = conversations.find(c => c.id === activeId) || null;

  // Persist
  useEffect(() => { saveConversations(conversations); }, [conversations]);
  useEffect(() => { saveActiveId(activeId); }, [activeId]);

  // Auto-scroll
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [activeConv?.messages?.length]);

  // Focus input
  useEffect(() => { inputRef.current?.focus(); }, [activeId]);

  // Close model picker on outside click
  useEffect(() => {
    if (!modelPickerOpen) return;
    const handler = (e: MouseEvent) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target as Node)) {
        setModelPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelPickerOpen]);

  // Switch model
  const switchModel = useCallback((modelValue: string) => {
    setCurrentModel(modelValue);
    setModelPickerOpen(false);
    try { localStorage.setItem(MODEL_KEY, modelValue); } catch {}
  }, []);

  // ============ 会话操作 ============
  const createConversation = useCallback(() => {
    const conv: Conversation = {
      id: generateId(), title: "新对话", messages: [],
      createdAt: Date.now(), updatedAt: Date.now(),
    };
    setConversations(prev => [conv, ...prev]);
    setActiveId(conv.id);
    setInput("");
  }, []);

  const deleteConversation = useCallback((id: string) => {
    setConversations(prev => prev.filter(c => c.id !== id));
    if (activeId === id) {
      const remaining = conversations.filter(c => c.id !== id);
      setActiveId(remaining.length > 0 ? remaining[0].id : null);
    }
  }, [activeId, conversations]);

  // ============ 发送消息 ============
  const sendMessage = useCallback(async () => {
    if ((!input.trim() && attachments.length === 0) || streaming) return;

    let convId = activeId;
    let convs = conversations;

    if (!convId) {
      const conv: Conversation = {
        id: generateId(), title: extractTitle(input),
        messages: [], createdAt: Date.now(), updatedAt: Date.now(),
      };
      convId = conv.id;
      convs = [conv, ...convs];
      setActiveId(convId);
    }

    const userMsg: Message = {
      id: generateId(), role: "user",
      content: input.trim() || `📎 ${attachments.length}个附件`,
      timestamp: Date.now(),
      _images: attachments.filter(a => a.type === "image").map(a => a.thumbnail || `data:${a.mime};base64,${a.data}`),
    };

    const assistantMsg: Message = {
      id: generateId(), role: "assistant", content: "", timestamp: Date.now(),
    };

    const isFirst = !convs.find(c => c.id === convId)?.messages?.length;

    setConversations(prev =>
      prev.map(c =>
        c.id === convId
          ? { ...c, title: isFirst ? extractTitle(input) : c.title,
              messages: [...c.messages, userMsg, assistantMsg], updatedAt: Date.now() }
          : c
      )
    );
    setInput("");
    setAttachments([]);
    setStreaming(true);

    const ctrl = new AbortController();
    setAbortCtrl(ctrl);

    try {
      const conv = convs.find(c => c.id === convId);
      const history = (conv?.messages || []).map(m => ({ role: m.role, content: m.content }));
      history.push({ role: "user", content: input.trim() });

      const resp = await fetch("/api/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: history, stream: true, model: currentModel,
          attachments: attachments.length > 0 ? attachments.map(a => ({
            name: a.name, type: a.type, data: a.data, mime: a.mime, size: a.size,
          })) : undefined,
        }),
        signal: ctrl.signal,
      });

      if (!resp.ok) { const err = await resp.text(); throw new Error(err.slice(0, 200)); }

      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      let fullContent = "";
      let buffer = "";

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            let data: string;
            if (trimmed.startsWith("data: ")) data = trimmed.slice(6);
            else if (trimmed.startsWith("data:")) data = trimmed.slice(5);
            else continue;
            if (data === "[DONE]") break;
            try {
              const json = JSON.parse(data);
              const delta = json.choices?.[0]?.delta?.content || "";
              if (delta) {
                fullContent += delta;
                const captured = fullContent;
                setConversations(prev =>
                  prev.map(c =>
                    c.id === convId
                      ? { ...c, messages: c.messages.map(m => m.id === assistantMsg.id ? { ...m, content: captured } : m) }
                      : c
                  )
                );
              }
            } catch {}
          }
        }
      }
    } catch (err: any) {
      if (err.name !== "AbortError") {
        const errMsg = err.message || "请求失败";
        setConversations(prev =>
          prev.map(c =>
            c.id === convId
              ? { ...c, messages: c.messages.map(m => m.id === assistantMsg.id ? { ...m, content: `⚠️ ${errMsg}` } : m) }
              : c
          )
        );
      }
    } finally {
      setStreaming(false);
      setAbortCtrl(null);
    }
  }, [input, streaming, activeId, conversations, attachments, currentModel]);

  // ============ 其他回调 ============
  const stopGeneration = useCallback(() => { abortCtrl?.abort(); }, [abortCtrl]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const copyMessage = (msg: Message) => {
    navigator.clipboard.writeText(msg.content);
    setCopiedId(msg.id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      if (attachments.length >= MAX_ATTACHMENTS) break;
      if (file.size > MAX_FILE_SIZE) continue;
      const isImage = IMAGE_TYPES.includes(file.type) || /\.(jpe?g|png|gif|webp)$/i.test(file.name);
      const isText = TEXT_TYPES.includes(file.type) || TEXT_EXTENSIONS.some(ext => file.name.toLowerCase().endsWith(ext));
      if (!isImage && !isText) continue;
      const reader = new FileReader();
      reader.onload = () => {
        const base64 = (reader.result as string).split(",")[1] || "";
        const preview: AttachmentPreview = {
          id: generateId(), name: file.name,
          type: isImage ? "image" : "file",
          mime: file.type || "application/octet-stream", size: file.size, data: base64,
          thumbnail: isImage ? reader.result as string : undefined,
        };
        setAttachments(prev => [...prev, preview]);
      };
      reader.readAsDataURL(file);
    }
    e.target.value = "";
  };

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id));
  };

  // ============ 渲染 ============
  return React.createElement("div", { className: "flex h-full bg-gradient-to-br from-gray-950 via-gray-900 to-gray-950 text-gray-100" }, [
    // ===== 侧边栏 =====
    React.createElement("div", { key: "sidebar", className: "w-64 border-r border-gray-800 flex flex-col shrink-0" }, [
      React.createElement("div", { key: "sb-top", className: "p-3 flex items-center justify-between" }, [
        React.createElement(Button, { key: "new-btn", onClick: createConversation, className: "justify-start gap-2 flex-1" }, [
          React.createElement(Plus, { key: "icon", size: 16 }),
          "新对话",
        ]),
        // Model picker trigger (in sidebar top bar)
        React.createElement("div", { key: "model-btn", className: "relative", ref: modelPickerRef }, [
          React.createElement("button", {
            key: "trigger", onClick: () => setModelPickerOpen(!modelPickerOpen),
            className: "flex items-center gap-1.5 px-2 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs text-gray-300 transition-colors"
          }, [
            React.createElement("span", { key: "label", className: "text-[#00d47e] font-medium" }, MODEL_DISPLAY[currentModel] || currentModel),
            React.createElement(ChevronDown, { key: "arrow", size: 14, className: modelPickerOpen ? "rotate-180" : "" }),
          ]),
          modelPickerOpen && React.createElement("div", {
            key: "dropdown", className: "absolute top-full right-0 mt-1 w-56 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl py-1 z-50"
          }, [
            ...MODELS.map(m =>
              React.createElement("button", {
                key: m.value, onClick: () => switchModel(m.value),
                className: `w-full text-left px-3 py-2 text-xs transition-colors ${currentModel === m.value ? "bg-[#00d47e]/20 text-[#00d47e]" : "text-gray-300 hover:bg-gray-800"}`
              }, [
                React.createElement("div", { key: "l", className: "font-medium" }, m.label),
                React.createElement("div", { key: "v", className: "text-[11px] text-gray-500 mt-0.5" }, m.value),
              ])
            ),
            React.createElement("div", { key: "divider", className: "border-t border-gray-700 mt-1 pt-1 px-3 py-1.5" },
              React.createElement("a", { href: "/config", className: "text-[11px] text-[#00d47e] hover:underline block" }, "⚙️ 在配置页管理模型")
            ),
          ]),
        ]),
      ]),
      React.createElement("div", { key: "sb-list", className: "flex-1 overflow-y-auto px-2 space-y-1" },
        conversations.map(conv =>
          React.createElement("div", {
            key: conv.id,
            className: `group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors ${conv.id === activeId ? "bg-[#00d47e]/15 text-[#00d47e]" : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"}`,
            onClick: () => setActiveId(conv.id),
          }, [
            React.createElement("span", { key: "title", className: "flex-1 truncate" }, conv.title),
            React.createElement("button", {
              key: "del", className: "opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 transition-opacity",
              onClick: (e: any) => { e.stopPropagation(); deleteConversation(conv.id); }
            }, React.createElement(Trash2, { size: 14 })),
          ])
        )
      ),
      React.createElement("div", { key: "sb-bottom", className: "p-3 text-xs text-gray-600 border-t border-gray-800 text-center" }, "vbit Agent · 本地智能助手"),
    ]),

    // ===== 主聊天区域 =====
    activeConv
      ? React.createElement("div", { key: "main", className: "flex-1 flex flex-col min-w-0" }, [
          // Messages
          React.createElement("div", { key: "msgs", className: "flex-1 overflow-y-auto px-4 py-6 space-y-4" }, [
            activeConv.messages.length === 0 && React.createElement("div", {
              key: "welcome", className: "flex flex-col items-center justify-center h-full text-gray-500 space-y-3"
            }, [
              React.createElement(Bot, { key: "bot", size: 48, className: "text-[#00d47e]/30" }),
              React.createElement("p", { key: "txt", className: "text-lg" }, "你好！我是 vbit Agent，有什么可以帮你？"),
              React.createElement("p", { key: "model", className: "text-sm text-gray-600" }, `当前模型：${MODEL_DISPLAY[currentModel] || currentModel}`),
            ]),
            ...activeConv.messages.map(msg =>
              React.createElement("div", {
                key: msg.id, className: `flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`
              }, [
                msg.role === "assistant" && React.createElement("div", {
                  key: "avatar", className: "shrink-0 w-8 h-8 rounded-full bg-[#00d47e]/20 flex items-center justify-center"
                }, React.createElement(Bot, { size: 16, className: "text-[#00d47e]" })),
                React.createElement("div", {
                  key: "bubble", className: `max-w-[75%] rounded-2xl px-4 py-3 text-sm relative group ${msg.role === "user" ? "bg-[#00d47e] text-gray-950" : "bg-gray-800 text-gray-100"}`
                }, [
                  msg.role === "assistant"
                    ? React.createElement("div", { key: "md", className: "prose prose-sm max-w-none" }, renderMarkdown(msg.content || (streaming ? "…" : "")))
                    : React.createElement(React.Fragment, null, [
                        React.createElement("div", { key: "txt", className: "whitespace-pre-wrap" }, msg.content),
                        ...(msg._images || []).map((img: string, ji: number) =>
                          React.createElement("img", { key: ji, src: img, alt: "", className: "max-w-[200px] max-h-[200px] rounded-lg mt-1" })
                        ),
                      ]),
                  msg.role === "assistant" && msg.content && !streaming && React.createElement("button", {
                    key: "copy", className: "absolute -bottom-5 right-2 opacity-0 group-hover:opacity-100 text-gray-500 hover:text-gray-300 transition-opacity",
                    onClick: () => copyMessage(msg), title: "复制"
                  }, copiedId === msg.id ? React.createElement(Check, { size: 12 }) : React.createElement(Copy, { size: 12 })),
                ]),
                msg.role === "user" && React.createElement("div", {
                  key: "uavatar", className: "shrink-0 w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center"
                }, React.createElement(User, { size: 16, className: "text-gray-300" })),
              ])
            ),
            React.createElement("div", { key: "end", ref: messagesEndRef }),
          ]),
          // Input area
          React.createElement("div", { key: "input-area", className: "border-t border-gray-800 p-4" }, [
            attachments.length > 0 && React.createElement("div", {
              key: "previews", className: "flex gap-2 mb-2 flex-wrap max-w-3xl mx-auto"
            }, attachments.map(att =>
              React.createElement("div", {
                key: att.id, className: "relative group flex items-center gap-1.5 bg-gray-800 rounded-lg px-2 py-1.5 text-xs"
              }, [
                att.type === "image" && att.thumbnail
                  ? React.createElement("img", { src: att.thumbnail, alt: att.name, className: "w-10 h-10 rounded object-cover" })
                  : React.createElement("span", { key: "icon", className: "text-gray-400" }, "📄"),
                React.createElement("span", { key: "name", className: "text-gray-300 max-w-[80px] truncate" }, att.name),
                React.createElement("button", {
                  key: "rm", className: "text-gray-500 hover:text-red-400 transition-colors",
                  onClick: () => removeAttachment(att.id)
                }, React.createElement(X, { size: 12 })),
              ])
            )),
            React.createElement("div", { key: "input-row", className: "flex items-end gap-2 max-w-3xl mx-auto" }, [
              React.createElement("input", {
                key: "file", ref: fileInputRef, type: "file", multiple: true,
                accept: "image/jpeg,image/png,image/gif,image/webp,.txt,.csv,.json,.md,.py,.js,.ts,.html,.css,.xml,.yaml,.yml,.toml,.sh,.log",
                className: "hidden", onChange: handleFileSelect,
              }),
              React.createElement("button", {
                key: "attach", className: "shrink-0 w-10 h-10 flex items-center justify-center rounded-xl text-gray-400 hover:text-[#00d47e] hover:bg-gray-800 transition-colors disabled:opacity-30",
                onClick: () => fileInputRef.current?.click(), disabled: streaming || attachments.length >= MAX_ATTACHMENTS, title: "添加附件"
              }, React.createElement(Paperclip, { size: 18 })),
              React.createElement("textarea", {
                key: "input", ref: inputRef, value: input,
                onChange: (e: any) => setInput(e.target.value),
                onKeyDown: handleKeyDown,
                placeholder: "输入消息…（Enter 发送，Shift+Enter 换行）",
                rows: 1,
                className: "flex-1 bg-gray-800 border border-gray-700 rounded-xl px-4 py-3 text-sm resize-none focus:outline-none focus:border-[#00d47e] focus:ring-1 focus:ring-[#00d47e]/50 transition-colors min-h-[44px] max-h-[120px]",
                style: { height: "auto" },
                onInput: (e: any) => { const el = e.currentTarget; el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 120) + "px"; },
                disabled: streaming,
              }),
              streaming
                ? React.createElement(Button, { key: "stop", onClick: stopGeneration, className: "rounded-xl px-4 bg-red-500 hover:bg-red-600 text-white", size: "icon" }, React.createElement(Square, { size: 16 }))
                : React.createElement(Button, { key: "send", onClick: sendMessage, disabled: !input.trim() && attachments.length === 0, className: "rounded-xl px-4 bg-[#00d47e] hover:bg-[#00b86a] text-gray-950 disabled:opacity-30", size: "icon" }, React.createElement(Send, { size: 16 })),
            ]),
          ]),
        ])
      : React.createElement("div", {
          key: "empty", className: "flex flex-col items-center justify-center h-full text-gray-500 space-y-4"
        }, [
          React.createElement(Bot, { key: "bot", size: 64, className: "text-[#00d47e]/20" }),
          React.createElement("p", { key: "txt", className: "text-xl" }, "开始新对话"),
          React.createElement("p", { key: "model", className: "text-sm text-gray-600" }, `当前模型：${MODEL_DISPLAY[currentModel] || currentModel}`),
          React.createElement(Button, {
            key: "btn", onClick: createConversation, className: "bg-[#00d47e] hover:bg-[#00b86a] text-gray-950 rounded-xl"
          }, [React.createElement(Plus, { key: "icon", size: 16, className: "mr-2" }), "新对话"]),
        ]),
  ]);
}
