import type { FieldSchema } from './generated/operations';

export function schemaFailure(value: unknown, schema: FieldSchema, location = 'response'): string | null {
  if (value === null) return schema.nullable ? null : `${location}: null is not allowed`;
  if (schema.enum && (typeof value !== 'string' || !schema.enum.includes(value))) return `${location}: value outside allowed enum`;
  switch (schema.type) {
    case 'string':
      if (typeof value !== 'string') return `${location}: expected string`;
      if (schema.minLength !== undefined && value.length < schema.minLength) return `${location}: string too short`;
      if (schema.maxLength !== undefined && value.length > schema.maxLength) return `${location}: string too long`;
      return null;
    case 'number': case 'integer':
      if (typeof value !== 'number' || !Number.isFinite(value)) return `${location}: expected finite number`;
      if (schema.type === 'integer' && !Number.isInteger(value)) return `${location}: expected integer`;
      if (schema.minimum !== undefined && value < schema.minimum) return `${location}: value below minimum`;
      if (schema.maximum !== undefined && value > schema.maximum) return `${location}: value above maximum`;
      return null;
    case 'boolean': return typeof value === 'boolean' ? null : `${location}: expected boolean`;
    case 'array':
      if (!Array.isArray(value)) return `${location}: expected array`;
      for (let i=0; i<value.length; i++) {
        const error = schema.items ? schemaFailure(value[i],schema.items,`${location}[${i}]`) : null;
        if (error) return error;
      }
      return null;
    case 'object':
      if (typeof value !== 'object' || value === null || Array.isArray(value)) return `${location}: expected object`;
      const record = value as Record<string, unknown>;
      for (const key of schema.required ?? []) {
        if (!Object.prototype.hasOwnProperty.call(record,key)) return `${location}.${key}: missing required field`;
      }
      for (const [key, child] of Object.entries(schema.properties ?? {})) {
        if (Object.prototype.hasOwnProperty.call(record,key)) {
          const error = schemaFailure(record[key],child,`${location}.${key}`);
          if (error) return error;
        }
      }
      return null;
    default: return null;
  }
}
