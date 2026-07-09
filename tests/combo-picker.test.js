// Tests for getComboCategories / getFreqMult / pickComboFromPool
// (the anti-repeat + frequency-weighted combo selection logic).

function resetState(){
  state = { _pickRound: { type: 'flow' }, recentCombos: [], usedCombosThisSession: new Set(), roundCategoryCount: {} };
  favCombos = new Set();
}

suite('getComboCategories', () => {
  test('detects feint', () => {
    assertEqual(getComboCategories('jab feint, 2'), ['feint']);
  });
  test('detects footwork (step)', () => {
    assertEqual(getComboCategories('step in, 1-2'), ['footwork']);
  });
  test('detects footwork (pivot)', () => {
    assertEqual(getComboCategories('pivot off lead foot, 2'), ['footwork']);
  });
  test('detects defense', () => {
    assertEqual(getComboCategories('slip inside, 2'), ['defense']);
  });
  test('plain number combo has no categories', () => {
    assertEqual(getComboCategories('1-2-3'), []);
  });
  test('can detect multiple categories at once', () => {
    const cats = getComboCategories('step in feint, 1-1-2');
    assert(cats.includes('feint'), 'expected feint');
    assert(cats.includes('footwork'), 'expected footwork');
  });
});

suite('getFreqMult', () => {
  test('high = 3x', () => { assertEqual(getFreqMult('high'), 3); });
  test('low = 0.35x', () => { assertEqual(getFreqMult('low'), 0.35); });
  test('normal/undefined = 1x', () => {
    assertEqual(getFreqMult('normal'), 1);
    assertEqual(getFreqMult(undefined), 1);
  });
});

suite('pickComboFromPool: frequency weighting', () => {
  test('a high-freq combo is picked roughly 3x as often as a normal-freq one', () => {
    resetState();
    const pool = [
      { n: '1-2', name: 'normal one', freq: 'normal' },
      { n: '1-1', name: 'high one', freq: 'high' },
    ];
    let highCount = 0;
    const N = 400;
    for (let i = 0; i < N; i++) {
      const pick = pickComboFromPool(pool);
      if (pick.nums === '1-1') highCount++;
    }
    // weights: normal=6*1=6, high=6*3=18 -> expected P(high) = 18/24 = 0.75
    const rate = highCount / N;
    assertInRange(rate, 0.60, 0.90, 'expected high-freq combo picked ~75% of the time, got rate ' + rate);
  });
});

suite('pickComboFromPool: anti-repeat', () => {
  // The exclusion loop only tries 12 random draws before falling back to a
  // filter that does NOT re-check recent/used -- so "never repeats" isn't a
  // hard guarantee, it's a strong statistical bias. Test it as one.
  test('overwhelmingly avoids the last 2 combos when a non-recent alternative exists', () => {
    resetState();
    state.recentCombos = ['1-2', '1-1'];
    const pool = [
      { n: '1-2', name: 'a', freq: 'normal' },
      { n: '1-1', name: 'b', freq: 'normal' },
      { n: '1-3', name: 'c', freq: 'normal' },
    ];
    let hits = 0;
    const N = 300;
    for (let i = 0; i < N; i++) {
      if (pickComboFromPool(pool).nums === '1-3') hits++;
    }
    assertInRange(hits / N, 0.9, 1.0, 'expected the non-recent combo picked ~99% of the time');
  });

  test('overwhelmingly skips used-this-session combos once the pool is large enough (>5)', () => {
    resetState();
    state.usedCombosThisSession = new Set(['1-2', '1-1', '1-2-1', '2-3', '3-2']);
    const pool = [
      { n: '1-2', name: 'a', freq: 'normal' },
      { n: '1-1', name: 'b', freq: 'normal' },
      { n: '1-2-1', name: 'c', freq: 'normal' },
      { n: '2-3', name: 'd', freq: 'normal' },
      { n: '3-2', name: 'e', freq: 'normal' },
      { n: '1-3-2', name: 'f', freq: 'normal' }, // the only unused one, pool size 6 (>5)
    ];
    let hits = 0;
    const N = 300;
    for (let i = 0; i < N; i++) {
      if (pickComboFromPool(pool).nums === '1-3-2') hits++;
    }
    assertInRange(hits / N, 0.75, 1.0, 'expected the unused combo picked the large majority of the time');
  });

  test('used-this-session exclusion is IGNORED when pool is small (<=5), to avoid starving a thin pool', () => {
    resetState();
    state.usedCombosThisSession = new Set(['1-2', '1-1']);
    const pool = [
      { n: '1-2', name: 'a', freq: 'normal' },
      { n: '1-1', name: 'b', freq: 'normal' },
    ];
    // both options are "used", pool.length (2) is NOT > 5, so the used-check should be skipped
    // and it should still return a valid pick instead of exhausting all attempts.
    const pick = pickComboFromPool(pool);
    assert(pick.nums === '1-2' || pick.nums === '1-1', 'expected a valid pick even though both are marked used');
  });
});

suite('pickComboFromPool: category caps', () => {
  test('skips a combo whose category is already at the round-type cap', () => {
    resetState();
    // warmup caps: {feint:0, footwork:1, defense:1}
    state._pickRound = { type: 'warmup' };
    state.roundCategoryCount = { defense: 1 }; // already at warmup's defense cap of 1
    const pool = [
      { n: 'slip inside, 2', name: 'defense move', freq: 'normal' }, // category: defense -> should be skipped
      { n: '1-2', name: 'plain', freq: 'normal' },
    ];
    for (let i = 0; i < 10; i++) {
      const pick = pickComboFromPool(pool);
      assertEqual(pick.nums, '1-2', 'should avoid the capped-out defense category');
    }
  });
});

suite('pickComboFromPool: fallback never crashes', () => {
  test('returns a valid combo even when every option is excluded for 12 straight attempts', () => {
    resetState();
    state.recentCombos = ['1-2']; // the only combo in the pool -- every attempt will be excluded
    const pool = [{ n: '1-2', name: 'only one', freq: 'normal' }];
    const pick = pickComboFromPool(pool);
    assert(pick && typeof pick.nums === 'string' && pick.nums.length > 0, 'expected a valid fallback pick, got ' + JSON.stringify(pick));
    assertEqual(pick.nums, '1-2');
  });
});
