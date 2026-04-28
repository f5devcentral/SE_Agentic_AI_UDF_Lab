import React, { useEffect, useState } from 'react';
import { useAuth } from 'react-oidc-context';

export default function App() {
  const auth = useAuth();
  const [prompt, setPrompt] = useState('');
  const [response, setResponse] = useState(null);

  useEffect(() => {
    // Attempt automatic login validation redirect mapped to the OBS Keycloak endpoint
    if (!auth.isAuthenticated && !auth.activeNavigator && !auth.isLoading && !auth.error) {
      auth.signinRedirect();
    }
  }, [auth]);

  const handleSearch = async () => {
    if (!auth.isAuthenticated) return;

    try {
      const res = await fetch('/api/plan', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // Explicitly extracting the Keycloak JWT structure and binding it for internal proxy resolution
          'Authorization': `Bearer ${auth.user?.access_token}`
        },
        body: JSON.stringify({ prompt: prompt })
      });
      const data = await res.json();
      setResponse(data);
    } catch (e) {
      console.error("Execution failed utilizing JWT scope.", e);
    }
  };

  if (auth.isLoading) {
    return <div>Provisioning Identity Validation...</div>;
  }

  if (auth.error) {
    return <div>Identity Validation Refused: {auth.error.message}</div>;
  }

  if (auth.isAuthenticated) {
    return (
      <div style={{ padding: '2rem' }}>
        <h1>Travel Query Gateway (Zero Trust)</h1>
        <div style={{ marginBottom: '1rem' }}>
          Identity Authenticated: <strong>{auth.user?.profile.preferred_username}</strong>
        </div>
        
        <textarea 
          placeholder="Input travel criteria (e.g. Find flights to Paris)"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          style={{ width: '100%', marginBottom: '1rem' }}
        />
        
        <button onClick={handleSearch} style={{ padding: '0.5rem 1rem' }}>
          Evaluate Travel Options
        </button>

        {response && (
          <pre style={{ marginTop: '2rem', padding: '1rem', background: '#f4f4f4' }}>
            {JSON.stringify(response, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  return <div>Executing OIDC Redirect Sequence...</div>;
}
