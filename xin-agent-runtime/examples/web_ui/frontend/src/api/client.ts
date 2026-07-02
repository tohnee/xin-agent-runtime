import { toast } from 'sonner';

export const getBaseUrl = () => localStorage.getItem('server_url') ?? '';
export const getUserId = () => localStorage.getItem('username') ?? '';
/**
 * JWT bearer token obtained from the backend auth endpoint.
 *
 * When present, the client sends `Authorization: Bearer <token>` and
 * the backend derives the caller identity from the JWT claims via
 * AuthMiddleware (which sets request.state.principal). The
 * `X-User-ID` header is NOT sent when a token is available, so the
 * client can no longer spoof identity via the header.
 *
 * When absent (dev mode without auth middleware mounted), the
 * client falls back to the `X-User-ID` header for local embeds.
 */
export const getToken = () => localStorage.getItem('auth_token') ?? '';

/**
 * Structured error thrown for non-2xx HTTP responses.
 * `message` contains the human-readable detail extracted from the backend.
 */
export class ApiError extends Error {
	readonly status: number;
	readonly detail: string;

	constructor(status: number, detail: string) {
		super(detail);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
	}
}

interface RequestOptions {
	method?: string;
	body?: unknown;
	params?: Record<string, string>;
	/** When true, suppresses the automatic error toast. Useful when the caller shows its own inline error UI. */
	silent?: boolean;
}

function buildHeaders(hasBody: boolean): Record<string, string> {
	const headers: Record<string, string> = {};
	if (hasBody) headers['Content-Type'] = 'application/json';

	const token = getToken();
	if (token) {
		// Authenticated: send JWT bearer token. The backend derives
		// user_id from request.state.principal (set by AuthMiddleware),
		// ignoring any X-User-ID header.
		headers['Authorization'] = `Bearer ${token}`;
	} else {
		// Dev-mode fallback: no JWT stored. Send X-User-ID so local
		// embeds without AuthMiddleware still work. The backend's
		// get_current_user_id accepts this only when no principal is
		// present on request.state.
		const userId = getUserId();
		if (userId) headers['X-User-ID'] = userId;
	}
	return headers;
}

/** Parse the response body and extract the `detail` field if the backend returned JSON. */
async function extractErrorDetail(res: Response): Promise<string> {
	const text = await res.text();
	try {
		const json = JSON.parse(text) as { detail?: unknown };
		if (typeof json.detail === 'string') return json.detail;
		if (json.detail !== undefined) return JSON.stringify(json.detail);
	} catch {
		// not JSON – fall through
	}
	return text || res.statusText;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
	const { method = 'GET', body, params, silent = false } = options;
	const url = new URL(path, getBaseUrl());
	if (params) {
		Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
	}

	const res = await fetch(url.toString(), {
		method,
		headers: buildHeaders(body !== undefined),
		body: body ? JSON.stringify(body) : undefined,
	});

	if (!res.ok) {
		const detail = await extractErrorDetail(res);
		const error = new ApiError(res.status, detail);
		if (!silent) toast.error(detail);
		throw error;
	}

	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

async function streamRequest(
	path: string,
	options: RequestOptions & { signal?: AbortSignal } = {},
): Promise<Response> {
	const { method = 'GET', body, params, signal, silent = false } = options;
	const url = new URL(path, getBaseUrl());
	if (params) {
		Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
	}

	const res = await fetch(url.toString(), {
		method,
		headers: buildHeaders(body !== undefined),
		body: body ? JSON.stringify(body) : undefined,
		signal,
	});

	if (!res.ok) {
		const detail = await extractErrorDetail(res);
		const error = new ApiError(res.status, detail);
		if (!silent) toast.error(detail);
		throw error;
	}

	return res;
}

export const client = {
	get: <T>(path: string, params?: Record<string, string>) =>
		request<T>(path, { method: 'GET', params }),
	post: <T>(path: string, body?: unknown, params?: Record<string, string>) =>
		request<T>(path, { method: 'POST', body, params }),
	patch: <T>(path: string, body?: unknown, params?: Record<string, string>) =>
		request<T>(path, { method: 'PATCH', body, params }),
	delete: <T = void>(path: string, params?: Record<string, string>) =>
		request<T>(path, { method: 'DELETE', params }),
	stream: (path: string, options?: RequestOptions & { signal?: AbortSignal }) =>
		streamRequest(path, options),
};
