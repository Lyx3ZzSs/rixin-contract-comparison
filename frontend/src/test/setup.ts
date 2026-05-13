import "@testing-library/jest-dom/vitest";

class TestDOMMatrix {
  a = 1;
  b = 0;
  c = 0;
  d = 1;
  e = 0;
  f = 0;

  translateSelf() {
    return this;
  }

  scaleSelf() {
    return this;
  }

  multiplySelf() {
    return this;
  }
}

if (!("DOMMatrix" in globalThis)) {
  Object.defineProperty(globalThis, "DOMMatrix", {
    value: TestDOMMatrix,
    writable: true,
  });
}
