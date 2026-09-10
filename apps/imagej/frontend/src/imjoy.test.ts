import { describe, expect, it, vi } from 'vitest'
import { readImageJState, runImageJMacro, snapshotImageJ, type ImageJApi } from './imjoy'

// The real ImageJA.JS RPC rejects every non-null macro return, even success.
const returned = (value: string) => vi.fn(async (source: string) => {
  const prefix = source.match(/__ATRIUM_RESULT_[a-f0-9]+__/)?.[0]
  throw prefix + value
})

function api(overrides: Partial<ImageJApi> = {}): ImageJApi {
  return { runMacro: returned('0'), getDimensions: vi.fn(),
    getImage: vi.fn(), viewImage: vi.fn(), selectWindow: vi.fn(), ...overrides }
}

describe('ImageJ RPC state and image export', () => {
  it('does not trigger ImageJ no-image dialogs while reading an empty instance', async () => {
    const target = api()
    expect(await readImageJState(target)).toEqual({ image_count: 0, current_image: null })
    expect(target.getDimensions).not.toHaveBeenCalled()
    expect(target.getImage).not.toHaveBeenCalled()
    await expect(snapshotImageJ(target)).rejects.toThrow('no open image')
    expect(target.getImage).not.toHaveBeenCalled()
  })

  it('preserves negative image identities and multiline titles returned by ImageJ', async () => {
    const target = api({ runMacro: returned('2\n-3\n96\n64\n1\n1\n1\nimage\nsecond line') })
    expect(await readImageJState(target)).toEqual({ image_count: 2,
      current_image: { id: -3, title: 'image\nsecond line', dimensions: [96, 64, 1, 1, 1] } })
    expect(target.getDimensions).not.toHaveBeenCalled()
  })

  it('propagates RPC failure instead of reporting zero images', async () => {
    const target = api({ runMacro: vi.fn().mockRejectedValue(new Error('RPC disconnected')) })
    await expect(readImageJState(target)).rejects.toThrow('RPC disconnected')
  })

  it('does not mistake an unmarked Java or macro error for a successful return', async () => {
    await expect(runImageJMacro(api({ runMacro: vi.fn().mockRejectedValue('Undefined identifier') }), 'bad()'))
      .rejects.toBe('Undefined identifier')
    await expect(runImageJMacro(api({ runMacro: vi.fn().mockResolvedValue(null) }), 'return "ok";'))
      .rejects.toThrow('did not confirm')
  })

  it('passes macro arguments through RPC and decodes only its own completion marker', async () => {
    const target = api({ runMacro: returned('hello\nworld') })
    expect(await runImageJMacro(target, 'return getArgument();', 'hello\nworld')).toBe('hello\nworld')
    expect(target.runMacro).toHaveBeenCalledWith(expect.stringContaining(' = getArgument();'), 'hello\nworld')
  })

  it('preserves function returns and comments while marking all main-program exits', async () => {
    const target = api({ runMacro: returned('ok') })
    await runImageJMacro(target, 'function f() { return "return ;"; }\n// return "comment";\nif (1) { return f(); } return "last";')
    const source = vi.mocked(target.runMacro).mock.calls[0]![0]
    expect(source).toContain('function f() { return "return ;"; }')
    expect(source).toContain('// return "comment";')
    expect(source).toMatch(/if \(1\) \{ \{ atriumResult\w+ = f\(\); return "__ATRIUM_RESULT_[a-f0-9]+__" \+ atriumResult\w+; \} \}/)
    expect(source).toMatch(/atriumResult\w+ = "last"; return "__ATRIUM_RESULT_[a-f0-9]+__" \+ atriumResult\w+;/)
  })

  it('exports only valid PNG bytes and preserves a typed-array byte offset', async () => {
    const bytes = new Uint8Array([0, 137, 80, 78, 71, 13, 10, 26, 10, 0])
    const target = api({ runMacro: returned('1\n-2\n96\n64\n1\n1\n1\nimage'),
      getImage: vi.fn().mockResolvedValue(bytes.subarray(1, 9)) })
    expect(await snapshotImageJ(target)).toBe('data:image/png;base64,iVBORw0KGgo=')
    expect(target.getImage).toHaveBeenCalledWith('png')
    vi.mocked(target.getImage).mockResolvedValue('not PNG bytes')
    await expect(snapshotImageJ(target)).rejects.toThrow('did not return PNG bytes')
  })
})
