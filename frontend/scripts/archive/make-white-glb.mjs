import fs from 'node:fs'

const input = process.argv[2]
const output = process.argv[3]
if (!input || !output) throw new Error('usage: node make-white-glb.mjs input.glb output.glb')

const source = fs.readFileSync(input)
const totalLength = source.readUInt32LE(8)
let offset = 12
let jsonChunk
let binaryChunk
while (offset < totalLength) {
  const length = source.readUInt32LE(offset)
  const type = source.readUInt32LE(offset + 4)
  const data = source.subarray(offset + 8, offset + 8 + length)
  if (type === 0x4e4f534a) jsonChunk = JSON.parse(data.toString('utf8'))
  if (type === 0x004e4942) binaryChunk = data
  offset += 8 + length
}
if (!jsonChunk || !binaryChunk) throw new Error('invalid GLB chunks')

for (const material of jsonChunk.materials ?? []) {
  material.pbrMetallicRoughness = {
    baseColorFactor: [0.88, 0.9, 0.94, 1],
    metallicFactor: 0.05,
    roughnessFactor: 0.72,
  }
  delete material.normalTexture
  delete material.occlusionTexture
  delete material.emissiveTexture
  delete material.emissiveFactor
  if (material.extensions) {
    const variants = material.extensions.KHR_materials_variants
    material.extensions = variants ? { KHR_materials_variants: variants } : undefined
    if (!material.extensions) delete material.extensions
  }
}
jsonChunk.extensionsUsed = (jsonChunk.extensionsUsed ?? []).filter((extension) => extension === 'EXT_meshopt_compression' || extension === 'KHR_mesh_quantization')
jsonChunk.extensionsRequired = (jsonChunk.extensionsRequired ?? []).filter((extension) => extension === 'EXT_meshopt_compression' || extension === 'KHR_mesh_quantization')

const json = Buffer.from(JSON.stringify(jsonChunk), 'utf8')
const jsonPadded = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)])
const binaryPadded = Buffer.concat([binaryChunk, Buffer.alloc((4 - (binaryChunk.length % 4)) % 4)])
const total = 12 + 8 + jsonPadded.length + 8 + binaryPadded.length
const header = Buffer.alloc(12)
header.write('glTF', 0, 4, 'ascii')
header.writeUInt32LE(2, 4)
header.writeUInt32LE(total, 8)
const jsonHeader = Buffer.alloc(8)
jsonHeader.writeUInt32LE(jsonPadded.length, 0)
jsonHeader.writeUInt32LE(0x4e4f534a, 4)
const binaryHeader = Buffer.alloc(8)
binaryHeader.writeUInt32LE(binaryPadded.length, 0)
binaryHeader.writeUInt32LE(0x004e4942, 4)
fs.writeFileSync(output, Buffer.concat([header, jsonHeader, jsonPadded, binaryHeader, binaryPadded]))
