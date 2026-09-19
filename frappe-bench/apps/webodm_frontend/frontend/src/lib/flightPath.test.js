import { describe, it, expect } from 'vitest'
import { sortImagesByCapture } from '@/lib/flightPath'

const gps = { latitude: 40.5, longitude: -73.9 }

describe('sortImagesByCapture', () => {
  it('orders timed images by capture_time ascending', () => {
    const out = sortImagesByCapture([
      { filename: 'b.jpg', capture_time: '2024-05-01 10:05:00', ...gps },
      { filename: 'a.jpg', capture_time: '2024-05-01 10:01:00', ...gps },
    ])
    expect(out.map(i => i.filename)).toEqual(['a.jpg', 'b.jpg'])
  })

  it('orders untimed images by natural filename order', () => {
    const out = sortImagesByCapture([
      { filename: 'DJI_0100.jpg', ...gps },
      { filename: 'DJI_0018.jpg', ...gps },
      { filename: 'DJI_0002.jpg', ...gps },
    ])
    expect(out.map(i => i.filename)).toEqual([
      'DJI_0002.jpg', 'DJI_0018.jpg', 'DJI_0100.jpg',
    ])
  })

  it('places timed images before untimed', () => {
    const out = sortImagesByCapture([
      { filename: 'z_untimed.jpg', ...gps },
      { filename: 'a_timed.jpg', capture_time: '2024-05-01 10:00:00', ...gps },
    ])
    expect(out.map(i => i.filename)).toEqual(['a_timed.jpg', 'z_untimed.jpg'])
  })

  it('drops images without usable GPS', () => {
    const out = sortImagesByCapture([
      { filename: 'ok.jpg', ...gps },
      { filename: 'nogps.jpg' },
      { filename: 'zero.jpg', latitude: 0, longitude: 0 },
    ])
    expect(out.map(i => i.filename)).toEqual(['ok.jpg'])
  })
})
