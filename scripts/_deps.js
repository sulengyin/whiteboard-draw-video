// 依赖解析：优先用当前项目里的 node_modules，其次 skill 自带。
// 这样脚本放在 skill 里也能跑项目的依赖，不必在 skill 目录再装一份。
const path = require('path');
const { createRequire } = require('module');

function loadDep(name) {
  const roots = [process.cwd(), __dirname, path.join(__dirname, '..')];
  for (const r of roots) {
    try {
      const req = createRequire(path.resolve(r, '__dep__.js'));
      return req(name);
    } catch (e) { /* 换下一个 */ }
  }
  return null;
}

module.exports = { loadDep };
