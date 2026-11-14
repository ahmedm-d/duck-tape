import test from "node:test";
import assert from "node:assert/strict";
import { greet, average } from "./service.ts";
test("greeting", () => assert.equal(greet(), "Hello world"));
test("average", () => assert.equal(average([2,4]), 3));
