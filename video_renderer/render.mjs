import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const [propsPath, outputPath, publicDir] = process.argv.slice(2);
if (!propsPath || !outputPath || !publicDir) {
  throw new Error('用法：node render.mjs <props.json> <output.mp4> <job-public-dir>');
}

const rendererRoot = path.dirname(fileURLToPath(import.meta.url));
const inputProps = JSON.parse(fs.readFileSync(propsPath, 'utf8'));
const compositionId = inputProps.compositionId || 'DynamicInfographic';
const browserCandidates = [
  process.env.REMOTION_BROWSER_EXECUTABLE,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean);
const browserExecutable = browserCandidates.find((candidate) => fs.existsSync(candidate));

const serveUrl = await bundle({
  entryPoint: path.join(rendererRoot, 'src', 'index.tsx'),
  rootDir: rendererRoot,
  publicDir: path.resolve(publicDir),
  webpackOverride: (configuration) => configuration,
});
const rendererOptions = browserExecutable ? {browserExecutable} : {};
const composition = await selectComposition({
  serveUrl,
  id: compositionId,
  inputProps,
  logLevel: 'warn',
  timeoutInMilliseconds: 120000,
  ...rendererOptions,
});
await renderMedia({
  serveUrl,
  composition,
  inputProps,
  codec: 'h264',
  outputLocation: path.resolve(outputPath),
  muted: true,
  crf: 19,
  pixelFormat: 'yuv420p',
  x264Preset: 'medium',
  concurrency: compositionId === 'MemoryHanddraw' ? 1 : '50%',
  overwrite: true,
  logLevel: 'warn',
  timeoutInMilliseconds: 120000,
  ...rendererOptions,
});
