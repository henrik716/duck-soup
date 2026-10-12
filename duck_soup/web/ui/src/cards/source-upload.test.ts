import { describe, expect, it } from 'vitest'
import { gdbRootFromUpload, groupUploadItems, isGdbInternalFile, sourceIdFromName, splitExt } from './source-upload'

const file = (name: string) => new File([''], name)
const names = (items: ReturnType<typeof groupUploadItems>) =>
  items.map(i => (i instanceof File ? i.name : `${i.shpFile.name}+[${i.sidecars.map(s => s.name).join(',')}]`))

describe('splitExt', () => {
  it('splits at the last dot and lowercases the extension', () => {
    expect(splitExt('Roads.GPKG')).toEqual({ base: 'Roads', ext: 'gpkg' })
    expect(splitExt('a.b.geojson')).toEqual({ base: 'a.b', ext: 'geojson' })
  })
  it('has no extension without a dot, or with only a leading one', () => {
    expect(splitExt('timestamps')).toEqual({ base: 'timestamps', ext: '' })
    expect(splitExt('.hidden')).toEqual({ base: '.hidden', ext: '' })
  })
})

describe('sourceIdFromName', () => {
  it('lowercases and replaces anything but [a-z0-9_]', () => {
    expect(sourceIdFromName('Bane NOR-spor 2024')).toBe('bane_nor_spor_2024')
  })
})

describe('isGdbInternalFile', () => {
  it.each(['a00000001.gdbtable', 'a00000001.gdbtablx', 'a00000004.CatItemsByType.atx', 'a00000009.spx', 'gdb', 'timestamps'])(
    'flags %s as the inside of a File Geodatabase', name => {
      expect(isGdbInternalFile(name)).toBe(true)
    },
  )
  it.each(['roads.shp', 'roads.atx', 'data.gdb.zip', 'parks.gpkg'])('passes %s through', name => {
    expect(isGdbInternalFile(name)).toBe(false)
  })
})

describe('groupUploadItems', () => {
  it('folds a shapefile\'s sidecars into its .shp, case-insensitively', () => {
    const items = groupUploadItems([file('Roads.SHP'), file('roads.dbf'), file('roads.shx'), file('roads.prj')])
    expect(names(items)).toEqual(['Roads.SHP+[roads.dbf,roads.shx,roads.prj]'])
  })
  it('keeps other files, and sidecars without a .shp, as they are', () => {
    const items = groupUploadItems([file('parks.gpkg'), file('lonely.dbf'), file('a.shp'), file('a.cpg')])
    expect(names(items)).toEqual(['parks.gpkg', 'lonely.dbf', 'a.shp+[a.cpg]'])
  })
})

describe('gdbRootFromUpload', () => {
  it('walks back out of the file\'s path inside the folder', () => {
    expect(gdbRootFromUpload('C:\\tmp\\up\\NVE.gdb\\a00000001.gdbtable', 'a00000001.gdbtable')).toBe('C:\\tmp\\up\\NVE.gdb')
    expect(gdbRootFromUpload('/tmp/up/NVE.gdb/sub/x.gdbtable', 'sub/x.gdbtable')).toBe('/tmp/up/NVE.gdb')
  })
})
