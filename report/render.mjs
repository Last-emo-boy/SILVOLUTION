// Render one HTML file to an A4 PDF with Chromium (Playwright). Footer: page n / total.
//   node report/render.mjs in.html out.pdf [1|0]
// PLAYWRIGHT_MJS: path of playwright's index.mjs (default: a checkout that has it installed).
import { pathToFileURL } from "node:url";
const pw = await import(process.env.PLAYWRIGHT_MJS || "file:///Users/w33d/Playground/old-man-sea-mv/node_modules/playwright/index.mjs");
const [inp, out, footer] = process.argv.slice(2);
const b = await pw.chromium.launch();
const p = await b.newPage();
await p.goto(pathToFileURL(inp).href, { waitUntil: "load" });
await p.evaluate(() => document.fonts.ready);
const foot = `<div style="width:100%;font-size:7.5pt;color:#7a7a76;font-family:'STHeiti','PingFang SC',sans-serif;padding:0 17mm;display:flex;justify-content:space-between">
  <span>硅演 Silicon Agent · 项目说明与运行记录</span><span>第 <span class="pageNumber"></span> 页 / 共 <span class="totalPages"></span> 页</span></div>`;
await p.pdf({
  path: out, format: "A4", printBackground: true, preferCSSPageSize: true,
  displayHeaderFooter: footer === "1", headerTemplate: "<div></div>", footerTemplate: foot,
});
await b.close();
