const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

async function expandLongTweet(page) {
  const expandSelectors = [
    'div[role="button"]:has-text("显示更多")',
    'div[role="button"]:has-text("Show more")',
    '[data-testid="tweet-text-show-more-link"]',
    'span:has-text("显示更多")',
    'span:has-text("Show more")'
  ];

  for (const selector of expandSelectors) {
    try {
      const btn = await page.$(selector);
      if (btn) {
        await btn.click();
        await page.waitForTimeout(1500);
        return true;
      }
    } catch (e) {
      continue;
    }
  }
  return false;
}

async function extractFormattedContent(page, baseUrl) {
  return await page.evaluate((base) => {
    const tweetElement = document.querySelector('[data-testid="tweetText"]') ||
                         document.querySelector('article [lang]') ||
                         document.querySelector('article');

    if (!tweetElement) return { text: '', html: '', formats: [], images: [], embeddedContent: [] };

    const images = [];
    const imgElements = document.querySelectorAll('[data-testid="tweetPhoto"] img, img[src*="pbs.twimg.com/media"]');
    imgElements.forEach(img => {
      let src = img.getAttribute('src') || img.getAttribute('data-src');
      if (src) {
        if (src.startsWith('//')) src = 'https:' + src;
        else if (src.startsWith('/')) src = base + src;
        if (!images.includes(src)) {
          images.push(src);
        }
      }
    });

    const codeBlocks = [];
    const embeddedContent = [];
    
    tweetElement.querySelectorAll('pre').forEach(pre => {
      const code = pre.querySelector('code') || pre;
      const text = code.textContent.trim();
      if (text) {
        let language = '';
        const codeEl = pre.querySelector('code');
        if (codeEl && codeEl.className) {
          const langMatch = codeEl.className.match(/language-(\w+)/);
          if (langMatch) language = langMatch[1];
        }
        
        if (!language && text.startsWith('{') && text.endsWith('}')) {
          language = 'json';
        } else if (!language && text.startsWith('<') && text.endsWith('>')) {
          language = 'html';
        } else if (!language && (text.includes('function ') || text.includes('const ') || text.includes('let '))) {
          language = 'javascript';
        } else if (!language && (text.includes('def ') || text.includes('import '))) {
          language = 'python';
        }
        
        embeddedContent.push({
          type: 'code',
          text: text,
          language: language,
          isMultiline: text.includes('\n') || text.length > 50
        });
        
        codeBlocks.push({
          text: text,
          language: language,
          isMultiline: true
        });
      }
    });
    
    tweetElement.querySelectorAll('code').forEach(c => {
      if (!c.closest('pre')) {
        const text = c.textContent.trim();
        if (text && (text.includes('\n') || text.length > 50)) {
          let language = '';
          if (text.startsWith('{') && text.endsWith('}')) {
            language = 'json';
          }
          embeddedContent.push({
            type: 'code',
            text: text,
            language: language,
            isMultiline: true
          });
          codeBlocks.push({
            text: text,
            language: language,
            isMultiline: true
          });
        }
      }
    });

    const formats = [];

    tweetElement.querySelectorAll('a').forEach(a => {
      let href = a.getAttribute('href');
      const text = a.textContent.trim();
      if (href && !href.includes('twitter.com/intent') && text) {
        if (href.startsWith('/')) {
          href = base + href;
        }
        if (!href.includes('/analytics') &&
            !href.includes('/status/') && text.match(/^\d/) === null &&
            !text.match(/^[\d,]+查看$/) &&
            !text.match(/^[\d,]+ views$/i) &&
            !text.match(/^[\d,]+$/) &&
            text.length > 1) {
          formats.push({
            type: 'link',
            text: text,
            href: href
          });
        }
      }
    });

    tweetElement.querySelectorAll('strong, b').forEach(b => {
      const text = b.textContent.trim();
      if (text && text.length > 1) {
        formats.push({ type: 'bold', text: text });
      }
    });

    tweetElement.querySelectorAll('code').forEach(c => {
      const text = c.textContent.trim();
      if (text && !c.closest('pre')) {
        const isMultiLine = text.includes('\n') || text.length > 50;
        if (!isMultiLine) {
          formats.push({ type: 'code', text: text });
        }
      }
    });

    tweetElement.querySelectorAll('em, i').forEach(i => {
      const text = i.textContent.trim();
      if (text && text.length > 1 && !i.closest('a') && !i.closest('code')) {
        formats.push({ type: 'italic', text: text });
      }
    });

    let text = tweetElement.textContent.trim();
    text = text.replace(/[\d,]+\s*查看/g, '');
    text = text.replace(/[\d,]+\s*views/gi, '');
    text = text.replace(/想发布自己的文章\？/g, '');
    text = text.replace(/升级为 Premium/g, '');
    text = text.replace(/[\d,]+\s*回复/g, '');
    text = text.replace(/[\d,]+\s*转帖/g, '');
    text = text.replace(/[\d,]+\s*喜欢/g, '');
    text = text.replace(/[\d,]+\s*书签/g, '');
    text = text.replace(/分享帖子/g, '');
    text = text.replace(/查看 \d+ 条回复/g, '');
    text = text.replace(/[\d,]+\.?\d*\s*万/g, '');
    text = text.replace(/上午\d+:\d+\s*·\s*\d+年\d+月\d+日/g, '');
    text = text.replace(/下午\d+:\d+\s*·\s*\d+年\d+月\d+日/g, '');
    text = text.replace(/\d+:\d+\s*[AP]M\s*·\s*\w+\s*\d+,\s*\d+/g, '');
    text = text.replace(/^[^\u4e00-\u9fa5a-zA-Z\[!\"]+/, '');
    text = text.replace(/[·•]\s*$/g, '');
    text = text.replace(/\s+/g, ' ').trim();

    let html = tweetElement.innerHTML;
    html = html.replace(/<svg[^>]*>.*?<\/svg>/gi, '');
    html = html.replace(/<button[^>]*data-testid="reply"[^>]*>.*?<\/button>/gi, '');
    html = html.replace(/<button[^>]*data-testid="retweet"[^>]*>.*?<\/button>/gi, '');
    html = html.replace(/<button[^>]*data-testid="like"[^>]*>.*?<\/button>/gi, '');
    html = html.replace(/<button[^>]*data-testid="bookmark"[^>]*>.*?<\/button>/gi, '');
    html = html.replace(/<a[^>]*analytics[^>]*>.*?<\/a>/gi, '');
    html = html.replace(/<a[^>]*premium_sign_up[^>]*>.*?<\/a>/gi, '');

    return {
      text: text,
      html: html,
      formats: formats,
      codeBlocks: codeBlocks,
      images: images,
      embeddedContent: embeddedContent
    };
  }, baseUrl);
}

function convertToMarkdown(content) {
  let markdown = content.text;

  const codeBlockPlaceholders = [];
  if (content.embeddedContent && content.embeddedContent.length > 0) {
    for (let i = 0; i < content.embeddedContent.length; i++) {
      const item = content.embeddedContent[i];
      if (item.type === 'code') {
        const placeholder = `__CODE_BLOCK_${i}__`;
        const lang = item.language || '';
        codeBlockPlaceholders.push({
          placeholder: placeholder,
          codeBlock: '\n```' + lang + '\n' + item.text + '\n```\n'
        });

        const fullText = item.text;
        if (markdown.includes(fullText)) {
          markdown = markdown.replace(fullText, placeholder);
        } else {
          const firstLine = fullText.split('\n')[0];
          if (markdown.includes(firstLine)) {
            const startIdx = markdown.indexOf(firstLine);
            let endIdx = startIdx;
            const lines = fullText.split('\n');
            for (const line of lines) {
              const lineIdx = markdown.indexOf(line, endIdx);
              if (lineIdx !== -1) {
                endIdx = lineIdx + line.length;
              }
            }
            if (endIdx > startIdx) {
              markdown = markdown.substring(0, startIdx) + placeholder + markdown.substring(endIdx);
            }
          }
        }
      }
    }
  } else if (content.codeBlocks && content.codeBlocks.length > 0) {
    for (let i = 0; i < content.codeBlocks.length; i++) {
      const block = content.codeBlocks[i];
      const placeholder = `__CODE_BLOCK_${i}__`;
      const lang = block.language || '';
      codeBlockPlaceholders.push({
        placeholder: placeholder,
        codeBlock: '\n```' + lang + '\n' + block.text + '\n```\n'
      });

      const fullText = block.text;
      if (markdown.includes(fullText)) {
        markdown = markdown.replace(fullText, placeholder);
      } else {
        const firstLine = fullText.split('\n')[0];
        if (markdown.includes(firstLine)) {
          const startIdx = markdown.indexOf(firstLine);
          let endIdx = startIdx;
          const lines = fullText.split('\n');
          for (const line of lines) {
            const lineIdx = markdown.indexOf(line, endIdx);
            if (lineIdx !== -1) {
              endIdx = lineIdx + line.length;
            }
          }
          if (endIdx > startIdx) {
            markdown = markdown.substring(0, startIdx) + placeholder + markdown.substring(endIdx);
          }
        }
      }
    }
  }

  const sortedFormats = [...content.formats].sort((a, b) => b.text.length - a.text.length);

  for (const format of sortedFormats) {
    if (format.type === 'bold') {
      markdown = markdown.replace(format.text, `**${format.text}**`);
    } else if (format.type === 'italic') {
      markdown = markdown.replace(format.text, `*${format.text}*`);
    } else if (format.type === 'code') {
      markdown = markdown.replace(format.text, `\`${format.text}\``);
    }
  }

  for (const format of sortedFormats) {
    if (format.type === 'link' && format.text) {
      markdown = markdown.replace(format.text, `[${format.text}](${format.href})`);
    }
  }

  for (const item of codeBlockPlaceholders) {
    markdown = markdown.replace(item.placeholder, item.codeBlock);
  }

  return markdown;
}

function saveToLocal(title, content, url, outputDir = '.') {
  const date = new Date();
  const dateStr = date.toISOString().split('T')[0];
  const safeTitle = title
    .replace(/[\/\\?%*:|"<>]/g, '-')
    .replace(/\s+/g, '_')
    .replace(/[：:""]/g, '_')
    .substring(0, 80);

  const baseFilename = `${dateStr}_${safeTitle}`;

  let markdown = `# ${title}\n\n`;
  markdown += `> 原始链接: ${url}\n`;
  markdown += `> 提取时间: ${date.toLocaleString('zh-CN')}\n\n`;
  markdown += `---\n\n`;

  if (content.images && content.images.length > 0) {
    markdown += `## 图片\n\n`;
    for (const img of content.images) {
      markdown += `![图片](${img})\n\n`;
    }
    markdown += `---\n\n`;
  }

  markdown += convertToMarkdown(content);

  const mdPath = path.join(outputDir, `${baseFilename}.md`);
  fs.writeFileSync(mdPath, markdown, 'utf-8');

  let imagesHtml = '';
  if (content.images && content.images.length > 0) {
    imagesHtml = `<div class="images">\n`;
    for (const img of content.images) {
      imagesHtml += `<img src="${img}" alt="图片" style="max-width: 100%; margin: 10px 0; border-radius: 8px;">\n`;
    }
    imagesHtml += `</div>\n<hr>\n`;
  }

  let processedHtml = content.html || '';
  processedHtml = processedHtml.replace(/href="\/([^"]+)"/g, `href="https://x.com/$1"`);
  processedHtml = processedHtml.replace(/src="\/([^"]+)"/g, `src="https://x.com/$1"`);

  let embeddedContentHtml = '';
  if (content.embeddedContent && content.embeddedContent.length > 0) {
    embeddedContentHtml = `<div class="embedded-content">\n`;
    for (const item of content.embeddedContent) {
      if (item.type === 'code') {
        const lang = item.language || '';
        const langLabel = lang ? `<span class="code-lang">${lang}</span>` : '';
        embeddedContentHtml += `<pre class="code-block">${langLabel}<code class="language-${lang || 'text'}">${escapeHtml(item.text)}</code></pre>\n`;
      }
    }
    embeddedContentHtml += `</div>\n`;
  }

  let html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; background: #fff; }
    h1 { border-bottom: 2px solid #1da1f2; padding-bottom: 10px; color: #0f1419; }
    .meta { color: #536471; font-size: 0.9em; margin-bottom: 20px; }
    .meta a { color: #1da1f2; text-decoration: none; }
    .meta a:hover { text-decoration: underline; }
    .images { margin: 20px 0; }
    .images img { max-width: 100%; margin: 10px 0; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .content { white-space: pre-wrap; color: #0f1419; }
    .content a { color: #1da1f2; text-decoration: none; }
    .content a:hover { text-decoration: underline; }
    .content code { background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-family: 'SF Mono', Monaco, monospace; font-size: 0.9em; }
    .content pre { background: #f4f4f4; padding: 12px; border-radius: 8px; overflow-x: auto; }
    .content pre code { background: none; padding: 0; }
    .content strong { font-weight: 700; }
    hr { border: none; border-top: 1px solid #e1e8ed; margin: 20px 0; }
    .embedded-content { margin: 20px 0; }
    .code-block { background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 8px; overflow-x: auto; margin: 15px 0; position: relative; }
    .code-block code { font-family: 'SF Mono', Monaco, 'Courier New', monospace; font-size: 0.9em; line-height: 1.5; white-space: pre; }
    .code-lang { position: absolute; top: 8px; right: 12px; font-size: 0.75em; color: #8b949e; text-transform: uppercase; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
    .language-json .token-string { color: #ce9178; }
    .language-json .token-number { color: #b5cea8; }
    .language-json .token-boolean { color: #569cd6; }
    .language-json .token-null { color: #569cd6; }
  </style>
</head>
<body>
  <h1>${title}</h1>
  <div class="meta">
    <p>原始链接: <a href="${url}" target="_blank">${url}</a></p>
    <p>提取时间: ${date.toLocaleString('zh-CN')}</p>
  </div>
  <hr>
  ${imagesHtml}
  ${embeddedContentHtml}
  <div class="content">${processedHtml}</div>
</body>
</html>`;

  const htmlPath = path.join(outputDir, `${baseFilename}.html`);
  fs.writeFileSync(htmlPath, html, 'utf-8');

  return { mdPath, htmlPath };
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function scrapeXContent(url, options = {}) {
  const { saveLocal = false, outputDir = '.' } = options;

  const browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--disable-gpu',
      '--disable-web-security',
      '--allow-running-insecure-content',
      '--disable-features=VizDisplayCompositor',
      '--disable-extensions',
      '--disable-ipc-flooding-protection',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
      '--disable-features=TranslateUI',
      '--disable-features=site-per-process,Translate,BlinkGenPropertyTrees',
      '--disable-back-forward-cache'
    ]
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
    timezoneId: 'Asia/Shanghai',
    locale: 'zh-CN,zh;q=0.9,en;q=0.8',
    geolocation: { longitude: 121.4737, latitude: 31.2304 },
    permissions: ['geolocation']
  });

  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', {
      get: () => undefined,
    });

    Object.defineProperty(navigator, 'plugins', {
      get: () => ({
        length: 3,
        0: { filename: 'internal-pdf-viewer' },
        1: { filename: 'adsfk-plugin' },
        2: { filename: 'internal-nacl-plugin' },
        refresh: () => {},
      }),
    });

    Object.defineProperty(navigator, 'languages', {
      get: () => ['zh-CN', 'zh', 'en'],
    });
  });

  const page = await context.newPage();

  try {
    await page.setExtraHTTPHeaders({
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
      'Accept-Encoding': 'gzip, deflate, br',
      'Connection': 'keep-alive',
      'Upgrade-Insecure-Requests': '1',
      'Sec-Fetch-Dest': 'document',
      'Sec-Fetch-Mode': 'navigate',
      'Sec-Fetch-Site': 'none',
      'Cache-Control': 'max-age=0'
    });

    console.log(`正在访问: ${url}`);
    const response = await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: 90000
    });

    if (response.status() !== 200 && response.status() !== 304) {
      console.log(`警告: 页面返回状态码 ${response.status()}`);
    }

    try {
      await Promise.race([
        page.waitForSelector('[data-testid="tweetText"]', { timeout: 15000 }),
        page.waitForSelector('article[role="article"]', { timeout: 15000 }),
        page.waitForSelector('article [lang]', { timeout: 15000 }),
        page.waitForTimeout(15000)
      ]);
    } catch (e) {
      console.log('提示: 等待元素超时，继续执行...');
    }

    console.log('尝试展开长文...');
    await expandLongTweet(page);
    await page.waitForTimeout(1000);

    const title = await page.title();
    console.log('提取格式化内容...');
    const content = await extractFormattedContent(page, 'https://x.com');

    if (!content.text) {
      const selectors = [
        '[data-testid="tweetText"]',
        'article div[lang]',
        '[data-testid="tweet"] div[dir="auto"]',
        'div[role="link"] div[dir="auto"]',
        'div[data-testid="cellInnerDiv"] div[lang]'
      ];

      for (const selector of selectors) {
        try {
          const elements = await page.$$(selector);
          for (const element of elements) {
            const text = await element.textContent();
            if (text && text.trim().length > content.text.length) {
              content.text = text.trim();
            }
          }
          if (content.text.length > 50) break;
        } catch (e) {
          continue;
        }
      }
    }

    const result = {
      success: true,
      title: title || 'X Post',
      content: content.text || '未能提取到内容',
      html: content.html,
      formats: content.formats,
      codeBlocks: content.codeBlocks,
      images: content.images,
      embeddedContent: content.embeddedContent || [],
      url: url
    };

    if (saveLocal && content.text) {
      const savedFiles = saveToLocal(result.title, content, url, outputDir);
      result.savedFiles = savedFiles;
      console.log(`已保存到本地:`);
      console.log(`  Markdown: ${savedFiles.mdPath}`);
      console.log(`  HTML: ${savedFiles.htmlPath}`);
    }

    console.log('提取结果:', JSON.stringify(result, null, 2));
    await browser.close();
    return result;

  } catch (error) {
    console.error('错误:', error.message);
    await browser.close();
    return {
      success: false,
      error: error.message,
      url: url
    };
  }
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const url = args.find(arg => !arg.startsWith('--'));
  const saveLocal = args.includes('--save-local');
  const outputDirIndex = args.indexOf('--output-dir');
  const outputDir = outputDirIndex !== -1 && args[outputDirIndex + 1] ? args[outputDirIndex + 1] : '.';

  if (!url) {
    console.log('用法: node x_content_scraper.js <X链接> [--save-local] [--output-dir <目录>]');
    console.log('');
    console.log('选项:');
    console.log('  --save-local    保存到本地 Markdown 和 HTML 文件');
    console.log('  --output-dir    指定输出目录 (默认为当前目录)');
    process.exit(1);
  }

  scrapeXContent(url, { saveLocal, outputDir }).then(result => {
    if (!saveLocal) {
      console.log(JSON.stringify(result));
    }
  });
}

module.exports = { scrapeXContent, saveToLocal, convertToMarkdown };
