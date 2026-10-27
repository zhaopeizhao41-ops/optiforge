// UI fixtures only. No Zemax access, optical computation, or project writes.
// Prescription values are from the repository's README doublet example.
export const surfaces = [
  { id: 1, name: '前表面', material: 'N-BK7', radius: '62.844', thickness: '8.000', type: 'Standard', conic: '0.000' },
  { id: 2, name: '胶合面', material: 'N-SF2', radius: '−39.605', thickness: '7.334', type: 'Standard', conic: '0.000' },
  { id: 3, name: '后表面', material: 'AIR', radius: '−122.520', thickness: '93.432', type: 'Standard', conic: '0.000' },
];

// Stable field identity for the schematic rays and analysis previews.
export const fieldSamples = [
  { id: 'axis', value: 0, name: '轴上', color: 'var(--st-field-axis)', dash: '' },
  { id: 'middle', value: .7, name: '0.7 视场', color: 'var(--st-field-middle)', dash: '7 4' },
  { id: 'edge', value: 1, name: '边缘', color: 'var(--st-field-edge)', dash: '2 4' },
];

export const wavelengthSamples = [
  { id: 'short', value: 486, color: 'var(--st-wave-short)' },
  { id: 'reference', value: 588, color: 'var(--st-wave-reference)' },
  { id: 'long', value: 656, color: 'var(--st-wave-long)' },
];

export const mtfSamples = [
  { frequency: 0, t: 1, s: 1 }, { frequency: 10, t: .97, s: .95 },
  { frequency: 20, t: .90, s: .88 }, { frequency: 30, t: .82, s: .78 },
  { frequency: 40, t: .72, s: .67 }, { frequency: 50, t: .61, s: .55 },
  { frequency: 60, t: .50, s: .43 }, { frequency: 70, t: .39, s: .32 },
  { frequency: 80, t: .29, s: .23 }, { frequency: 90, t: .20, s: .15 },
  { frequency: 100, t: .12, s: .09 },
];
