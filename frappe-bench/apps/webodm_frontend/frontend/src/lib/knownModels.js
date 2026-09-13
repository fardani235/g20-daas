// Helpers for the curated model list exposed by the analysis catalog. Choosing a
// known model fills its labels, family, label offset, and recommended
// parameters, so users don't type filenames or know the family.

export function applyModelChoice(params, model) {
  return {
    ...params,
    model: model.model,
    labels: model.labels,
    ...(model.family ? { family: model.family } : {}),
    ...(model.label_offset != null ? { label_offset: model.label_offset } : {}),
    ...(model.recommended || {}),
  }
}

export function matchingModel(models, params) {
  if (!Array.isArray(models)) return null
  return models.find(m => m.model === params?.model && m.labels === params?.labels) || null
}
