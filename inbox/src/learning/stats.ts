/** Wilson score interval lower bound for a binomial proportion. Returns 0 when n is 0. */
export function wilsonLowerBound(successes: number, n: number, z: number): number {
  if (n <= 0) return 0;
  if (successes < 0 || successes > n) throw new RangeError("successes must be within 0..n");
  const p = successes / n;
  const z2 = z * z;
  const centre = p + z2 / (2 * n);
  const spread = z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return Math.max(0, (centre - spread) / (1 + z2 / n));
}

export function mean(values: readonly number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}
