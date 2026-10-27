import { describe, expect, it } from 'vitest'
import { describeProblemPath, parseProblems } from './problem-path'

describe('parseProblems', () => {
  it('reads the path and message of an engine error', () => {
    const detail = "mapping 'width' (expr): there's no column 'x' after the last step\npipelines.0.mapping.1\n  there's no column 'x' after the last step"
    expect(parseProblems(detail)).toEqual([
      { path: 'pipelines.0.mapping.1', msg: "there's no column 'x' after the last step" },
    ])
  })

  it('drops pydantic noise', () => {
    const detail = '1 validation error for Config\npipelines.0.steps.3.source\n  Field required [type=missing, input_value={}]\n'
    expect(parseProblems(detail)).toEqual([{ path: 'pipelines.0.steps.3.source', msg: 'Field required' }])
  })
})

describe('describeProblemPath', () => {
  it('numbers list items from 1, as the editor shows them', () => {
    expect(describeProblemPath('pipelines.0.mapping.1')).toBe('mapping row 2')
    expect(describeProblemPath('pipelines.0.steps.7.aggregates.0')).toBe('step 8 · aggregate 1')
    expect(describeProblemPath('pipelines.0.layers.0.mapping.3')).toBe('output layer 1 · mapping row 4')
  })

  it('names fields', () => {
    expect(describeProblemPath('pipelines.0.sources.2.crs')).toBe('source 3 · crs')
    expect(describeProblemPath('pipelines.0.working_crs')).toBe('working CRS')
    expect(describeProblemPath('pipelines.0.steps.1.fields.n')).toBe("step 2 · field 'n'")
  })

  it('names the pipeline only when asked', () => {
    expect(describeProblemPath('pipelines.1.steps.0', true)).toBe('pipeline 2 · step 1')
  })
})
