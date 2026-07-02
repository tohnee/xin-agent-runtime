import { useState } from 'react';

import { Button } from '@/components/ui/button.tsx';
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from '@/components/ui/card.tsx';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field.tsx';
import { Input } from '@/components/ui/input.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';
import { cn } from '@/lib/utils.ts';

interface Props {
	onComplete: () => void;
	className?: string;
}

export const SetupPage = ({ onComplete, className }: Props) => {
	const { t } = useTranslation();
	const [url, setUrl] = useState(() => localStorage.getItem('server_url') ?? '');
	const [username, setUsername] = useState(() => localStorage.getItem('username') ?? '');
	const [authToken, setAuthToken] = useState(() => localStorage.getItem('auth_token') ?? '');

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault();
		localStorage.setItem('server_url', url);
		localStorage.setItem('username', username);
		// Store JWT token. When present, the client sends
		// `Authorization: Bearer <token>` and the backend derives
		// user_id from JWT claims via AuthMiddleware. When absent,
		// the client falls back to `X-User-ID` (dev mode only).
		if (authToken) {
			localStorage.setItem('auth_token', authToken);
		} else {
			localStorage.removeItem('auth_token');
		}
		onComplete();
	};

	return (
		<div className="flex items-center justify-center h-full">
			<div className={cn('flex flex-col gap-6 w-full max-w-sm', className)}>
				<Card>
					<CardHeader>
						<CardTitle>{t('setup.title')}</CardTitle>
						<CardDescription>{t('setup.description')}</CardDescription>
					</CardHeader>
					<CardContent>
						<form onSubmit={handleSubmit}>
							<FieldGroup>
								<Field>
									<FieldLabel htmlFor="server-url-input">
										{t('setup.serverUrl')}
									</FieldLabel>
									<Input
										id="server-url-input"
										type="url"
										placeholder={t('setup.serverUrlPlaceholder')}
										value={url}
										onChange={(e) => setUrl(e.target.value)}
										required
									/>
								</Field>
								<Field>
									<FieldLabel htmlFor="username-input">
										{t('setup.username')}
									</FieldLabel>
									<Input
										id="username-input"
										type="text"
										placeholder={t('setup.usernamePlaceholder')}
										value={username}
										onChange={(e) => setUsername(e.target.value)}
										required
									/>
								</Field>
								<Field>
									<FieldLabel htmlFor="auth-token-input">
										{t('setup.authToken')}
									</FieldLabel>
									<Input
										id="auth-token-input"
										type="password"
										placeholder={t('setup.authTokenPlaceholder')}
										value={authToken}
										onChange={(e) => setAuthToken(e.target.value)}
									/>
									<FieldDescription>
										{t('setup.authTokenHint')}
									</FieldDescription>
								</Field>
								<Field>
									<Button type="submit" className="w-full">
										{t('setup.submit')}
									</Button>
								</Field>
							</FieldGroup>
						</form>
					</CardContent>
				</Card>
				<FieldDescription className="px-6 text-center">{t('setup.hint')}</FieldDescription>
			</div>
		</div>
	);
};
