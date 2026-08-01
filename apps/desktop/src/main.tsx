import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const el = document.getElementById('root');
if (!el) throw new Error('#root 不存在 —— index.html 被改坏了');

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
