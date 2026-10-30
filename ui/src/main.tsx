import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
const cache = new QueryClient({defaultOptions:{queries:{retry:false, staleTime:10_000, refetchOnWindowFocus:false},mutations:{retry:false}}});
const node = document.getElementById('root');
if (!node) throw new Error('Studio cockpit root is missing');
createRoot(node).render(<React.StrictMode><QueryClientProvider client={cache}><App /></QueryClientProvider></React.StrictMode>);
