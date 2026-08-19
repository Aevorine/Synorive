import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { preloadDisplayFont } from './lib/fontPreload';

// 标题字体分片和 React 同时开始读。放在 createRoot 之前——
// 挂载之后再插 preload，字体请求就排在首屏渲染后面了，等于没预取。
preloadDisplayFont();

const el = document.getElementById('root');
if (!el) throw new Error('#root 不存在 —— index.html 被改坏了');

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
