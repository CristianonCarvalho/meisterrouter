#!/usr/bin/env node

/**
 * MeisterRouter — Node.js CLI & NPX Runner
 * Encapsula o runtime Python e garante auto-bootstrap transparente no ecossistema npm / npx.
 */

const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const rootDir = path.resolve(__dirname, '..');
const localVenvBin = path.join(rootDir, '.venv', 'bin', 'meister');
const userLocalBin = path.join(os.homedir(), '.local', 'bin', 'meister');

function findMeister() {
  if (fs.existsSync(localVenvBin)) return localVenvBin;
  if (fs.existsSync(userLocalBin)) return userLocalBin;

  const whichCmd = os.platform() === 'win32' ? 'where' : 'which';
  const res = spawnSync(whichCmd, ['meister'], { encoding: 'utf8' });
  if (res.status === 0 && res.stdout.trim()) {
    return res.stdout.trim().split('\n')[0];
  }
  return null;
}

let meisterBin = findMeister();

if (!meisterBin) {
  console.log('🔮 MeisterRouter executável não encontrado no sistema. Executando bootstrap automático...');
  const installScript = path.join(rootDir, 'bin', 'install.sh');
  if (fs.existsSync(installScript)) {
    const installRes = spawnSync('bash', [installScript], { stdio: 'inherit' });
    if (installRes.status !== 0) {
      console.error('❌ Falha ao executar bootstrap do MeisterRouter.');
      process.exit(installRes.status || 1);
    }
    meisterBin = findMeister();
  }
}

if (!meisterBin) {
  console.error('❌ MeisterRouter não pôde ser inicializado. Certifique-se de ter Python 3.10+ instalado no seu sistema.');
  process.exit(1);
}

// Repassa todos os argumentos diretamente para o executável meister
const args = process.argv.slice(2);
const child = spawn(meisterBin, args, { stdio: 'inherit', env: process.env });

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code || 0);
  }
});
