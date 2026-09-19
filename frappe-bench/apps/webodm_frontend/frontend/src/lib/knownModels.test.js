import { describe, it, expect } from 'vitest'
import { applyModelChoice, matchingModel } from './knownModels.js'

const DEEPFOREST = {
  id: 'deepforest-tree',
  model: 'deepforest.onnx',
  labels: 'tree.txt',
  family: 'torchvision',
  label_offset: 0,
  recommended: { tile_size: 256, overlap: 64, confidence: 0.3 },
}

describe('knownModels', () => {
  it('applies model, labels, family, offset and recommended params', () => {
    const out = applyModelChoice({ confidence: 0.9, classes: ['tree'] }, DEEPFOREST)
    expect(out).toEqual({
      classes: ['tree'],
      model: 'deepforest.onnx',
      labels: 'tree.txt',
      family: 'torchvision',
      label_offset: 0,
      tile_size: 256,
      overlap: 64,
      confidence: 0.3,
    })
  })

  it('matches a model by filename and labels', () => {
    const models = [DEEPFOREST, { id: 'coco', model: 'yolov8n.onnx', labels: 'coco.txt' }]
    expect(matchingModel(models, { model: 'deepforest.onnx', labels: 'tree.txt' }).id)
      .toBe('deepforest-tree')
    expect(matchingModel(models, { model: 'custom.onnx', labels: 'x.txt' })).toBeNull()
    expect(matchingModel(undefined, {})).toBeNull()
  })
})
