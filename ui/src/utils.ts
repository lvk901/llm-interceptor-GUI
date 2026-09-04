import type {
  ExchangeDetail,
  NormalizedExchange,
  NormalizedMessage,
  NormalizedTool,
  RawRequest,
  RawResponse,
  Session,
  SessionOverview,
  RequestResponsePair,
} from './types';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const asString = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback;

/**
 * Detects if the request body follows OpenAI structure.
 */
const isOpenAIFormat = (body: unknown): boolean => {
  if (!isRecord(body)) return false;

  // OpenAI Responses requests carry input/instructions at the top level
  // instead of the Chat Completions messages array.
  if (typeof body.model === 'string' && ('input' in body || 'instructions' in body || 'response_format' in body)) {
    return true;
  }

  // Check for OpenAI specific tool format
  const tools = body.tools;
  if (
    Array.isArray(tools) &&
    tools.some((t) => isRecord(t) && t.type === 'function')
  ) {
    return true;
  }

  // Check for OpenAI specific message roles or properties
  const messages = body.messages;
  if (
    Array.isArray(messages) &&
    messages.some(
      (m) =>
        isRecord(m) &&
        (m.role === 'tool' || m.role === 'developer' || 'tool_calls' in m)
    )
  ) {
    return true;
  }

  // If system is strictly in messages and not at top level (Anthropic uses top level system usually)
  if (!('system' in body) && Array.isArray(messages) && messages.some((m) => isRecord(m) && m.role === 'system')) {
    return true;
  }

  return false;
};

/**
 * Normalize provider-specific token usage stats into {input_tokens, output_tokens}.
 */
const normalizeUsageMetrics = (rawUsage: unknown) => {
  if (!isRecord(rawUsage)) return undefined;

  const safeNumber = (value: unknown): number | undefined =>
    typeof value === 'number' && Number.isFinite(value) ? value : undefined;

  const inputRaw = isRecord(rawUsage) ? rawUsage.input_tokens ?? rawUsage.prompt_tokens : undefined;
  const outputRaw = isRecord(rawUsage) ? rawUsage.output_tokens ?? rawUsage.completion_tokens : undefined;
  const totalRaw = isRecord(rawUsage) ? rawUsage.total_tokens : undefined;

  let input = safeNumber(inputRaw);
  let output = safeNumber(outputRaw);
  const total = safeNumber(totalRaw);

  if (input === undefined && output === undefined && total === undefined) {
    return undefined;
  }

  if (input === undefined && total !== undefined && output !== undefined) {
    input = Math.max(total - output, 0);
  }

  if (output === undefined && total !== undefined && input !== undefined) {
    output = Math.max(total - input, 0);
  }

  const inputFinal = input ?? total ?? 0;
  const outputFinal = output ?? 0;
  const totalFinal = total ?? (inputFinal + outputFinal);

  return {
    input_tokens: inputFinal,
    output_tokens: outputFinal,
    total_tokens: totalFinal,
  };
};

/**
 * Convert OpenAI system content (string/array/object) to readable string.
 */
const extractProviderTextContent = (content: unknown): string => {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === 'string') return block;
        if (isRecord(block)) {
          if (typeof block.text === 'string') return block.text;
          return JSON.stringify(block, null, 2);
        }
        return String(block);
      })
      .join('\n');
  }
  if (isRecord(content)) return JSON.stringify(content, null, 2);
  if (content === undefined || content === null) return '';
  return String(content);
};

/**
 * Normalizes an OpenAI-style request body into our standard format.
 */
const normalizeOpenAIRequest = (
  body: unknown
): { system: string | undefined; messages: NormalizedMessage[]; tools: NormalizedTool[]; model: string } => {
  const model = isRecord(body) ? asString(body.model, 'unknown-model') : 'unknown-model';

  const bodyRecord = isRecord(body) ? body : {};
  const rawMessages = Array.isArray(bodyRecord.messages)
    ? bodyRecord.messages
    : Array.isArray(bodyRecord.input)
      ? bodyRecord.input
      : typeof bodyRecord.input === 'string'
        ? [{ role: 'user', content: bodyRecord.input }]
      : [];

  // 1. Extract System Prompt (OpenAI puts it in messages)
  const systemMessages = rawMessages.filter(
    (m) => isRecord(m) && (m.role === 'system' || m.role === 'developer')
  );
  const instructionText = 'instructions' in bodyRecord
    ? extractProviderTextContent(bodyRecord.instructions).trim()
    : '';
  const messageSystemText = systemMessages
    .map((m) => (isRecord(m) ? extractProviderTextContent(m.content) : ''))
    .filter(Boolean)
    .join('\n');
  const system = [instructionText, messageSystemText].filter(Boolean).join('\n') || undefined;

  // 2. Normalize Tools
  const toolsSrc = Array.isArray(bodyRecord.tools) ? bodyRecord.tools : [];
  const tools: NormalizedTool[] = toolsSrc
    .map((t) => {
      if (!isRecord(t)) return null;
      // OpenAI Tool format: { type: 'function', function: { name, description, parameters } }
      if (t.type === 'function') {
        const fn = isRecord(t.function) ? t.function : t;
        const tool: NormalizedTool = {
          name: asString(fn.name, 'unknown'),
          input_schema: fn.parameters ?? fn.input_schema,
        };
        if (typeof fn.description === 'string') {
          tool.description = fn.description;
        }
        return tool;
      }
      return null;
    })
    .filter((t): t is NormalizedTool => t !== null);

  // 3. Normalize Messages (Convert OpenAI structure to "Normalized" Anthropic-like structure for UI)
  const messages: NormalizedMessage[] = rawMessages
    .filter((m) => !(isRecord(m) && (m.role === 'system' || m.role === 'developer')))
    .flatMap((m) => {
      const role = isRecord(m) ? asString(m.role, 'user') : 'user';

      if (isRecord(m) && m.type === 'function_call') {
        let input: unknown = {};
        const argsRaw = asString(m.arguments, '{}');
        try { input = JSON.parse(argsRaw); } catch { input = { error: 'Failed to parse arguments', raw: argsRaw }; }
        return [{ role: 'assistant', content: [{ type: 'tool_use', name: asString(m.name, 'unknown'), input, id: m.call_id ?? m.id }] }];
      }

      if (isRecord(m) && m.type === 'function_call_output') {
        return [{ role: 'user', content: [{ type: 'tool_result', tool_use_id: m.call_id ?? m.id, content: m.output }] }];
      }

      // Handle Assistant with Tool Calls
      if (role === 'assistant' && isRecord(m) && Array.isArray(m.tool_calls)) {
        const contentBlocks: Record<string, unknown>[] = [];
        if (m.content) {
          contentBlocks.push({ type: 'text', text: m.content });
        }
        m.tool_calls.forEach((tc) => {
          if (!isRecord(tc)) return;
          const fn = isRecord(tc.function) ? tc.function : null;
          const argsRaw = fn ? asString(fn.arguments, '{}') : '{}';

          let input: unknown = {};
          try {
            input = JSON.parse(argsRaw);
          } catch {
            input = { error: 'Failed to parse arguments', raw: argsRaw };
          }

          contentBlocks.push({
            type: 'tool_use',
            name: fn ? asString(fn.name, 'unknown') : 'unknown',
            input,
            id: tc.id,
          });
        });
        return [{ role: 'assistant', content: contentBlocks }];
      }

      // Handle Tool Results (OpenAI 'tool' role -> Normalized 'user' role with tool_result block)
      if (role === 'tool' && isRecord(m)) {
        return [{
          role: 'user',
          content: [
            {
              type: 'tool_result',
              tool_use_id: m.tool_call_id,
              content: m.content,
            },
          ],
        }];
      }

      // Standard User/Assistant Text
      const messageContent = isRecord(m) ? m.content : m;
      // Responses API input messages use `input_text`; the chat renderer uses
      // the provider-neutral `text` block shape.
      const normalizedContent = Array.isArray(messageContent)
        ? messageContent.map((block) =>
            isRecord(block) && block.type === 'input_text'
              ? { ...block, type: 'text' }
              : block
          )
        : messageContent;
      return [{
        role: role as NormalizedMessage['role'],
        content: normalizedContent,
      }];
    });

  return { system, messages, tools, model };
};

/**
 * Normalizes an Anthropic-style request body into our standard format.
 */
const normalizeAnthropicRequest = (
  body: unknown
): { system: unknown; messages: NormalizedMessage[]; tools: NormalizedTool[]; model: string } => {
  if (!isRecord(body)) return { system: undefined, messages: [], tools: [], model: 'unknown' };

  const model = asString(body.model, 'unknown-model');

  // System prompt can be a string or an array of content blocks in Anthropic.
  // Preserve arrays so the chat/system views can render each block separately.
  const system: unknown =
    typeof body.system === 'string' || Array.isArray(body.system) ? body.system : undefined;

  const messages: NormalizedMessage[] = Array.isArray(body.messages)
    ? body.messages
        .filter((message): message is Record<string, unknown> => isRecord(message))
        .map((message) => ({
          role: asString(message.role, 'user') as NormalizedMessage['role'],
          content: message.content,
        }))
    : [];

  const tools: NormalizedTool[] = Array.isArray(body.tools)
    ? body.tools
        .map((t) => {
          if (!isRecord(t)) return null;
          const tool: NormalizedTool = {
            name: asString(t.name, 'unknown'),
            input_schema: t.input_schema,
          };
          if (typeof t.description === 'string') {
            tool.description = t.description;
          }
          return tool;
        })
        .filter((t): t is NormalizedTool => t !== null)
    : [];

  return { system, messages, tools, model };
};

const normalizeExchangePair = (
  pair: RequestResponsePair,
  index: number,
  fallbackSessionId: string,
  sequenceId?: string
): NormalizedExchange | null => {
  if (!pair.request) return null;

  const rawRequest: RawRequest = {
    type: 'request',
    id: pair.request.request_id,
    timestamp: pair.request.timestamp,
    method: pair.request.method || 'POST',
    url: pair.request.url || '',
    headers: pair.request.headers || {},
    body: pair.request.body,
  };

  const rawResponse: RawResponse | null = pair.response
    ? {
        type: 'response',
        request_id: pair.response.request_id,
        timestamp: pair.response.timestamp,
        status_code: pair.response.status_code || 0,
        latency_ms: pair.response.latency_ms || 0,
        body: pair.response.body,
      }
    : null;

  let responseContent: unknown = rawResponse?.body;
  const usageData: unknown =
    isRecord(rawResponse?.body) && 'usage' in rawResponse.body ? rawResponse.body.usage : undefined;

  try {
    const openAIFormat = isOpenAIFormat(rawRequest.body);
    const normalized = openAIFormat
      ? normalizeOpenAIRequest(rawRequest.body)
      : normalizeAnthropicRequest(rawRequest.body);

    if (openAIFormat) {
      if (isRecord(rawResponse?.body) && Array.isArray(rawResponse.body.choices)) {
        const choice = rawResponse.body.choices[0];
        if (isRecord(choice) && isRecord(choice.message)) {
          const msg = choice.message;
          if (Array.isArray(msg.tool_calls)) {
            const blocks: Record<string, unknown>[] = [];
            if (msg.content) {
              blocks.push({ type: 'text', text: msg.content });
            }
            msg.tool_calls.forEach((tc) => {
              if (!isRecord(tc) || !isRecord(tc.function)) return;
              const argsRaw = asString(tc.function.arguments, '');
              let input: unknown = {};
              if (argsRaw) {
                try {
                  input = JSON.parse(argsRaw);
                } catch {
                  input = { error: 'Failed to parse arguments', raw: argsRaw };
                }
              }
              blocks.push({
                type: 'tool_use',
                name: asString(tc.function.name, 'unknown'),
                input,
                id: tc.id,
              });
            });
            responseContent = blocks;
          } else {
            responseContent = msg.content;
          }
        }
      } else if (isRecord(rawResponse?.body) && Array.isArray(rawResponse.body.output)) {
        const blocks: Record<string, unknown>[] = [];
        rawResponse.body.output.forEach((item) => {
          if (!isRecord(item)) return;
          if (item.type === 'function_call') {
            const argsRaw = asString(item.arguments, '{}');
            let input: unknown = {};
            try {
              input = JSON.parse(argsRaw);
            } catch {
              input = { error: 'Failed to parse arguments', raw: argsRaw };
            }
            blocks.push({
              type: 'tool_use',
              name: asString(item.name, 'unknown'),
              input,
              id: item.call_id ?? item.id,
            });
            return;
          }
          if (item.type !== 'message' || !Array.isArray(item.content)) return;
          item.content.forEach((part) => {
            if (!isRecord(part)) return;
            if (part.type === 'output_text' && typeof part.text === 'string') {
              blocks.push({ type: 'text', text: part.text });
            } else if (part.type === 'refusal' && typeof part.refusal === 'string') {
              blocks.push({ type: 'text', text: part.refusal });
            }
          });
        });
        if (blocks.length > 0) {
          responseContent = blocks;
        } else if (typeof rawResponse.body.output_text === 'string') {
          responseContent = rawResponse.body.output_text;
        }
      } else if (isRecord(rawResponse?.body) && typeof rawResponse.body.output_text === 'string') {
        responseContent = rawResponse.body.output_text;
      }
    } else if (isRecord(rawResponse?.body)) {
      responseContent = 'content' in rawResponse.body ? (rawResponse.body as Record<string, unknown>).content : rawResponse.body;
    }

    const { system, messages, tools, model } = normalized;
    const systemPromptKey = extractProviderTextContent(system);

    return {
      id: rawRequest.id || `${fallbackSessionId}-${sequenceId || index + 1}`,
      sequenceId: sequenceId || String(index + 1).padStart(5, '0'),
      timestamp: rawRequest.timestamp || new Date().toISOString(),
      latencyMs: rawResponse?.latency_ms || 0,
      statusCode: rawResponse?.status_code || 0,
      model,
      systemPromptKey,
      toolNames: [],
      hasFullDetails: true,
      systemPrompt: system,
      messages,
      tools,
      responseContent,
      usage: normalizeUsageMetrics(usageData),
      rawRequest,
      rawResponse,
    };
  } catch (e) {
    console.error(`Error processing request ${index} in session ${fallbackSessionId}`, e);
    return null;
  }
};

export const normalizeSessionOverview = (overview: SessionOverview): Session => {
  const exchanges: NormalizedExchange[] = overview.exchanges.map((exchange) => ({
    id: exchange.id || `${overview.id}-${exchange.sequence_id}`,
    sequenceId: exchange.sequence_id,
    timestamp: exchange.timestamp || new Date().toISOString(),
    latencyMs: exchange.latency_ms || 0,
    statusCode: exchange.status_code || 0,
    model: exchange.model || 'unknown-model',
    systemPromptKey: exchange.system_prompt_key || '',
    toolNames: exchange.tool_names || [],
    hasFullDetails: false,
    systemPrompt: undefined,
    messages: [],
    tools: [],
    responseContent: null,
    usage: normalizeUsageMetrics(exchange.usage),
    rawRequest: {
      type: 'request',
      id: exchange.id || `${overview.id}-${exchange.sequence_id}`,
      timestamp: exchange.timestamp || new Date().toISOString(),
      method: exchange.request_method || 'POST',
      url: exchange.request_url || '',
      headers: {},
      body: null,
    },
    rawResponse: exchange.has_response
      ? {
          type: 'response',
          request_id: exchange.id,
          timestamp: exchange.timestamp || new Date().toISOString(),
          status_code: exchange.status_code || 0,
          latency_ms: exchange.latency_ms || 0,
          body: null,
        }
      : null,
  }));

  return {
    id: overview.id,
    name: overview.id,
    exchanges,
  };
};

export const normalizeExchangeDetail = (detail: ExchangeDetail, sessionId: string): NormalizedExchange | null => {
  const normalized = normalizeExchangePair(detail.pair, Number(detail.sequence_id) - 1, sessionId, detail.sequence_id);
  if (!normalized) return null;
  normalized.id = detail.id || normalized.id;
  return normalized;
};

export const mergeExchangeDetail = (
  session: Session,
  detailedExchange: NormalizedExchange
): Session => ({
  ...session,
  exchanges: session.exchanges.map((exchange) =>
    exchange.sequenceId === detailedExchange.sequenceId
      ? {
          ...exchange,
          ...detailedExchange,
          systemPromptKey: exchange.systemPromptKey || detailedExchange.systemPromptKey,
          toolNames: exchange.toolNames.length > 0 ? exchange.toolNames : detailedExchange.toolNames,
          hasFullDetails: true,
        }
      : exchange
  ),
});

export const formatTimestamp = (iso: string) => {
  if (!iso) return '--:--:--';
  try {
    const date = new Date(iso);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
};

export const formatDuration = (ms: number) => {
  if (!Number.isFinite(ms) || ms <= 0) return '0s';

  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 1) return '<1s';
  if (totalSeconds < 60) return `${totalSeconds}s`;

  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
};
