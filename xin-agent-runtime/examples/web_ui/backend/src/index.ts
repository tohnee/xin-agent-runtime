import express from 'express';
import cors from 'cors';

const app = express();
const PORT = process.env.PORT || 3000;

// CORS whitelist — never echo back arbitrary origins.
const allowedOrigins = process.env.CORS_ORIGIN
	? process.env.CORS_ORIGIN.split(',').map((s) => s.trim())
	: [];

const corsOptions: cors.CorsOptions = {
	origin: (origin, callback) => {
		// Allow same-origin/no-origin requests (server-to-server, curl).
		if (!origin) return callback(null, true);
		if (allowedOrigins.length === 0) {
			return callback(
				new Error('CORS_ORIGIN not configured; request blocked'),
			);
		}
		if (allowedOrigins.includes(origin)) {
			return callback(null, true);
		}
		return callback(new Error(`Origin ${origin} not allowed by CORS`));
	},
	credentials: true,
	methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
	allowedHeaders: ['Content-Type', 'Authorization', 'X-User-ID'],
};

app.use(cors(corsOptions));
app.use(express.json());

app.get('/api/health', (_req, res) => {
	res.json({ status: 'ok' });
});

app.listen(PORT, () => {
	console.log(`Server running on http://localhost:${PORT}`);
});
