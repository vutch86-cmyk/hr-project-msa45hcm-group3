import { PublicClientApplication } from '@azure/msal-browser';
import api from '../api/client';

let clientPromise: Promise<PublicClientApplication | null> | undefined;

export function getMicrosoftClient(): Promise<PublicClientApplication | null> {
  if (!clientPromise) {
    clientPromise = (async () => {
      const { data } = await api.get('/auth/microsoft/config');
      if (!data.enabled) return null;
      const client = new PublicClientApplication({
        auth: {
          clientId: data.client_id,
          authority: `https://login.microsoftonline.com/${data.tenant_id}`,
          redirectUri: `${window.location.origin}/microsoft-callback.html`,
        },
        cache: { cacheLocation: 'memoryStorage' },
      });
      await client.initialize();
      return client;
    })().catch((error) => {
      clientPromise = undefined;
      throw error;
    });
  }
  return clientPromise;
}
