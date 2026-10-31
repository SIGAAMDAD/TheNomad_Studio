import type { StudioApiOperations, StudioOperationId, Error as StudioError } from '../../CONTRACTS/api.types';
import { operations } from './generated/operations';
import { schemaFailure } from './schema';

export class StudioApiError extends Error {
  constructor(
    public readonly kind: 'server' | 'network' | 'protocol',
    public readonly status: number | null,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null = null,
    public readonly relatedRefs: readonly string[] = [],
  ) { super(message); this.name = 'StudioApiError'; }
}
export type Invocation<K extends StudioOperationId> = {
  params: StudioApiOperations[K]['params'];
  query?: StudioApiOperations[K]['query'];
  body?: StudioApiOperations[K]['body'];
  signal?: AbortSignal;
};

export type HttpFetch = typeof fetch;
export class StudioClient {
  constructor(
    private readonly getToken: () => string,
    private readonly baseUrl: string = '',
    private readonly http: HttpFetch = fetch,
  ) { }

  async call<K extends StudioOperationId>(operation: K, input: Invocation<K>): Promise<StudioApiOperations[K]['response']> {
    const meta = operations[operation];
    const parameters = input.params as Record<string, string>;
    const url = meta.path.replace(/\{([^{}]+)\}/g, (_match: string, key: string) => {
      if (!Object.prototype.hasOwnProperty.call(parameters, key) || !parameters[key]) {
        throw new StudioApiError('protocol', null, 'missing_path_parameter', `Missing path parameter: ${key}`);
      }
      return encodeURIComponent(parameters[key]);
    });
    const query = new URLSearchParams();
    const values = input.query as Record<string, string | number | boolean | undefined> | undefined;
    for (const field of meta.query) {
      const value = values?.[field.name];
      if (value !== undefined && value !== null) query.set(field.name, String(value));
      else if (field.required) throw new StudioApiError('protocol', null, 'missing_query', `Missing query: ${field.name}`);
    }
    const requestUrl = `${this.baseUrl.replace(/\/$/, '')}${url}${query.size ? `?${query}` : ''}`;
    const token = this.getToken();
    if (!token) throw new StudioApiError('protocol', null, 'unauthenticated', 'Enter a bearer token for the configured operator API.');
    if (meta.body && (input.body === undefined || input.body === null)) {
      throw new StudioApiError('protocol', null, 'missing_body', `Missing ${operation} request body.`);
    }
    // No client-side automatic retries: an ambiguous mutation outcome must remain UNKNOWN.
    let response: Response;
    try {
      response = await this.http(requestUrl, {
        method: meta.method,
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', ...(meta.body ? { 'Content-Type': 'application/json' } : {}) },
        ...(meta.body ? { body: JSON.stringify(input.body) } : {}),
        signal: input.signal,
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
      });
    } catch (cause) {
      if (input.signal?.aborted) throw cause;
      throw new StudioApiError('network', null, meta.method === 'GET' ? 'network_unavailable' : 'unknown_effect',
        meta.method === 'GET' ? 'The API response could not be observed.' : 'Request outcome UNKNOWN. Inspect the exact effect before retrying.');
    }
    let payload: unknown;
    try { payload = await response.json(); }
    catch {
      throw new StudioApiError('protocol', response.status, 'invalid_json', 'API returned no valid JSON response.');
    }
    if (!response.ok) {
      const error = parseServerError(payload);
      if (error) throw new StudioApiError('server', response.status, error.code, error.message, error.request_id, error.related_refs);
      throw new StudioApiError('protocol', response.status, 'unexpected_error', `HTTP ${response.status}: invalid error envelope.`);
    }
    if (!isRecord(payload) || Array.isArray(payload)) {
      throw new StudioApiError('protocol', response.status, 'invalid_response', `Unexpected successful response to ${operation}.`);
    }
    const schemaError = schemaFailure(payload, meta.response);
    if (schemaError) throw new StudioApiError('protocol', response.status, 'schema_mismatch', schemaError);
    return payload as unknown as StudioApiOperations[K]['response'];
  }
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function parseServerError(value: unknown): StudioError | null {
  if (!isRecord(value) || typeof value.code !== 'string' || typeof value.message !== 'string') return null;
  return {
    code: value.code as StudioError['code'], message: value.message,
    request_id: typeof value.request_id === 'string' ? value.request_id : null,
    related_refs: Array.isArray(value.related_refs) ? value.related_refs.filter((ref): ref is string => typeof ref === 'string') : [],
  };
}
