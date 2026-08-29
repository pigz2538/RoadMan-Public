import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const sourceRoot = join(frontendRoot, 'node_modules', 'three', 'examples', 'jsm', 'libs', 'draco', 'gltf')
const targetRoot = join(frontendRoot, 'public', 'draco')
const decoderFiles = ['draco_decoder.js', 'draco_decoder.wasm', 'draco_wasm_wrapper.js']

mkdirSync(targetRoot, { recursive: true })
for (const filename of decoderFiles) {
  copyFileSync(join(sourceRoot, filename), join(targetRoot, filename))
}
writeFileSync(
  join(targetRoot, 'NOTICE.txt'),
  [
    'Draco 3D Data Compression decoder',
    'Copyright 2016 Google Inc.',
    'Licensed under the Apache License, Version 2.0.',
    'https://www.apache.org/licenses/LICENSE-2.0',
    'Source: https://github.com/google/draco',
    '',
  ].join('\n'),
)
