import fs from 'node:fs'

const input = process.argv[2]
const output = process.argv[3]
if (!input || !output) throw new Error('usage: node sanitize-glb.mjs input.glb output.glb')

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

const expensive = [
  'KHR_materials_clearcoat',
  'KHR_materials_emissive_strength',
  'KHR_materials_iridescence',
  'KHR_materials_transmission',
]
for (const material of jsonChunk.materials ?? []) {
  for (const extension of expensive) delete material.extensions?.[extension]
  if (material.extensions && Object.keys(material.extensions).length === 0) delete material.extensions
}
jsonChunk.extensionsUsed = (jsonChunk.extensionsUsed ?? []).filter((extension) => !expensive.includes(extension))
jsonChunk.extensionsRequired = (jsonChunk.extensionsRequired ?? []).filter((extension) => !expensive.includes(extension))

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
