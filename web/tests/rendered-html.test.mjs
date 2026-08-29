import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the whiteboard video application", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /<title>有温度出品<\/title>/i);
  assert.match(html, /把你的表达，画成一支会说话的白板视频/);
  assert.match(html, /上传 MiniMax 克隆音频/);
  assert.match(html, /人物参考/);
  assert.match(html, /上传人物参考图/);
  assert.match(html, /开始生成视频/);
  assert.match(html, /API 设置/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/);
});

test("keeps public defaults portable and free of local configuration", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /minimax_base_url:"https:\/\/api\.minimaxi\.com"/);
  assert.match(page, /minimax_api_key:""/);
  assert.doesNotMatch(page, /IndexTTS|tts_url|tts_mode/);
  assert.match(page, /api_key:""/);
  assert.doesNotMatch(page, /192\.168\.|10\.\d+\.\d+\.\d+/);
  assert.match(layout, /title:\s*"有温度出品"/);
  assert.match(packageJson, /"build": "vinext build"/);
  assert.match(packageJson, /"test": "npm run build/);
});

test("registers the memory handdraw template as available after renderer review", async () => {
  const [page, preview] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/styles/memory-handdraw-placeholder.svg", import.meta.url), "utf8"),
  ]);

  assert.match(page, /name:"岁月回忆手绘风"/);
  assert.match(page, /badge:"新增",supportedModes:\["standard"\]/);
  assert.doesNotMatch(page, /supportedModes:\["standard"\],disabled:true/);
  assert.match(preview, /线稿 → 低饱和彩色/);
  assert.match(preview, /效果示意/);
});

test("lets users hide and restore style cards without deleting templates", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /HIDDEN_STYLES_KEY="whiteboard-maker:hidden-styles:v1"/);
  assert.match(page, /localStorage\.setItem\(HIDDEN_STYLES_KEY/);
  assert.match(page, /管理显示/);
  assert.match(page, /显示全部/);
  assert.match(page, /至少保留一个通用画面风格/);
  assert.match(page, /modeStyleOptions\.filter\(item=>!hiddenStyles\.has\(item\.name\)\)/);
  assert.match(css, /\.styleCard\.styleHidden/);
  assert.match(css, /\.styleManagerBar/);
});
test("keeps character references optional in standard production", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(page, /pageMode!=="infographic"&&<section className="characterReferenceSection"/);
  assert.match(page, /人物参考 \{pageMode==="standard"&&<em>可选<\/em>\}/);
  assert.match(page, /pageMode!=="infographic"&&readyCharacters\.length/);
  assert.match(page, /body\.append\("character_manifest"/);
  assert.match(page, /MAX_CHARACTER_IMAGE_BYTES=15\*1024\*1024/);
});
