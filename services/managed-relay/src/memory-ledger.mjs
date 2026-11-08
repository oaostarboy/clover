// Synchronous, isolate-local weighted reservations. A reservation's owner must
// keep it until every allocation it represents is no longer retained.
export class MemoryLedger {
  constructor(limit) {
    if (!Number.isSafeInteger(limit) || limit < 1) throw Error('invalid memory limit');
    this.limit = limit;
    this.used = 0;
  }

  reserve(weight) {
    if (!Number.isSafeInteger(weight) || weight < 0) throw Error('invalid reservation');
    if (weight > this.limit - this.used) return null;
    this.used += weight;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.used -= weight;
    };
  }
}
