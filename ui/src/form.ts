import type { FieldSchema, OperationMeta } from './generated/operations';

export type Fields = Record<string, unknown>;
export function defaults(meta: OperationMeta, projectId: string): { params: Record<string, string>; body: Fields; query: Fields } {
  const params: Record<string, string> = {};
  for (const field of meta.pathParams) params[field] = field === 'project_id' ? projectId : '';
  const body: Fields = {};
  for (const [key, schema] of Object.entries(meta.body?.properties ?? {})) {
    if (key === 'request_id') body[key] = crypto.randomUUID();
    else if (schema.default !== undefined) body[key] = schema.default;
    else if (schema.type === 'array') body[key] = [];
    else if (schema.type === 'object') body[key] = {};
    else if (schema.type === 'number' || schema.type === 'integer') body[key] = 0;
    else body[key] = '';
  }
  return { params, body, query: {} };
}
export function castValue(raw: string, field: FieldSchema): unknown {
  if (field.type === 'array' || field.type === 'object') {
    const parsed = JSON.parse(raw);
    if (field.type === 'array' ? !Array.isArray(parsed) : typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('Expected valid ' + field.type + ' JSON.');
    return parsed;
  }
  if (field.type === 'integer' || field.type === 'number') {
    if (!raw.trim()) throw new Error('A number is required.');
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || (field.type === 'integer' && !Number.isInteger(parsed))) throw new Error('Invalid ' + field.type + '.');
    if (field.minimum !== undefined && parsed < field.minimum) throw new Error(`Minimum is ${field.minimum}.`);
    if (field.maximum !== undefined && parsed > field.maximum) throw new Error(`Maximum is ${field.maximum}.`);
    return parsed;
  }
  if (field.type === 'boolean') return raw === 'true';
  if (field.enum && !field.enum.includes(raw)) throw new Error('Choose a permitted value.');
  return raw;
}
export function validateRequired(values: Fields, required: readonly string[] | undefined): string[] {
  return (required ?? []).filter((name) => !Object.prototype.hasOwnProperty.call(values, name) || values[name] === '' || values[name] === null || values[name] === undefined);
}
export function normalizeStatus(value: string): 'unknown' | 'pending' | 'observed' | 'terminal' {
  const v = value.toLowerCase();
  if (v.includes('unknown') || v.includes('quiet') || v.includes('unverified')) return 'unknown';
  if (v.includes('observed') || v.includes('published') || v.includes('delivered') || v === 'pass' || v === 'ready') return 'observed';
  if (v.includes('cancel') || v.includes('failed') || v.includes('rejected') || v.includes('stopped')) return 'terminal';
  return 'pending';
}
