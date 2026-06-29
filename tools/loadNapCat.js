const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');

const hasNapcatParam = process.argv.includes('--no-sandbox');
const package = require('/Applications/QQ.app/Contents/Resources/app/package.json');

if (hasNapcatParam) {
  (async () => {
    const napcatPath = path.join(
      os.homedir(),
      'Library/Containers/com.tencent.qq/Data/Documents/napcat/napcat.mjs'
    );
    await import(pathToFileURL(napcatPath).href);
  })();
} else {
  require('/Applications/QQ.app/Contents/Resources/app/app_launcher/index.js');
  setImmediate(() => {
    global.launcher.installPathPkgJson.main = ((version) => {
      if (version >= 29271) return './application.asar/app_launcher/index.js';
      if (version >= 28060) return './application/app_launcher/index.js';
      return './app_launcher/index.js';
    })(package.buildVersion);
  });
}
