// The accessibility check of PatentRef checklist 7.6: axe-core on every rendered page (lapse_tools/a11y/render_pages.py),
// with the WCAG 2.0, 2.1 and 2.2 A and AA rules and axe's best practices, in the light and the dark color scheme, at 1280 and
// 320 pixels wide. It also checks that nothing scrolls sideways at 320 pixels. Exit 1 when any page has a serious or
// critical violation or scrolls sideways; minor and moderate findings are listed but do not fail.
//   cd lapse_tools/a11y && npm ci && node axe_check.js <dir of .html files>
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");
const axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];
const FAIL = new Set(["serious", "critical"]);

(async () => {
  const dir = path.resolve(process.argv[2] || "pages");
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".html")).sort();
  if (!files.length) { console.error(`no .html files in ${dir}`); process.exit(2); }
  const browser = await puppeteer.launch({ headless: true, args: ["--no-sandbox"] });
  let bad = 0, findings = 0;
  for (const f of files) {
    for (const scheme of ["light", "dark"]) {
      for (const width of [1280, 320]) {
        const page = await browser.newPage();
        await page.setViewport({ width, height: 900 });
        await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
        // no network: the pages' one external script (the sign-up page's auth library) is not needed for the markup
        await page.setRequestInterception(true);
        page.on("request", (r) => (r.url().startsWith("file:") ? r.continue() : r.abort()));
        await page.goto("file://" + path.join(dir, f), { waitUntil: "load" });
        await page.addScriptTag({ content: axeSource });
        const res = await page.evaluate(async (tags) => {
          const r = await window.axe.run(document, { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"] });
          return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target.join(" ")).slice(0, 5) }));
        }, TAGS);
        const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        for (const v of res) {
          findings++;
          const failing = FAIL.has(v.impact);
          if (failing) bad++;
          console.log(`${failing ? "FAIL" : "note"} ${f} ${scheme} ${width}px ${v.impact} ${v.id}: ${v.help} [${v.nodes.join(", ")}]`);
        }
        if (width === 320 && sideways > 0) { bad++; console.log(`FAIL ${f} ${scheme} 320px scrolls sideways by ${sideways}px`); }
        await page.close();
      }
    }
  }
  await browser.close();
  console.log(`AXE: ${bad ? "FAIL" : "PASS"} (${files.length} pages x 2 color schemes x 2 widths, axe-core ${require("axe-core/package.json").version}, ` +
              `tags ${TAGS.join(",")}; serious or critical: ${bad}; all findings: ${findings})`);
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
