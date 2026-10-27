import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {podcastRecommendations} from './podcast-recommendations.mjs';

test('browses all categories with verified episodes, short reasons and a shared cache', async () => {
 const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'podcast-picks-'));
 const titles = new Map();
 let calls = 0;
 const client = {responses: {create: async args => {
  calls++;
  const {episodes} = JSON.parse(args.input.replace('Select episodes as JSON: ', ''));
  const picks = episodes.map(e => ({id: e.id, summary: 'Discover the surprising idea behind this story.'}));
  return {output_text: JSON.stringify({picks: [
   {id: 9999, summary: 'An episode that does not actually exist.'},
   {id: episodes[0].id, summary: 'Too short'},
   ...picks,
   picks[0]
  ]})};
 }}};
 const load = podcastRecommendations(dir, client, {fetch: async url => ({ok: true, json: async () => {
  const parsed = new URL(url);
  if (parsed.pathname === '/lookup') {
   const id = parsed.searchParams.get('id');
   return {results: [{kind: 'podcast-episode', trackName: 'A real episode from ' + titles.get(id), description: 'A substantial conversation about a surprising discovery.', episodeUrl: 'https://example.com/' + id + '.mp3', trackTimeMillis: 1800000}]};
  }
  const query = parsed.searchParams.get('term');
  const id = String(titles.size + 1);
  titles.set(id, query);
  return {results: [{collectionId: Number(id), collectionName: query, feedUrl: 'https://example.com/feed/' + id}]};
 }})});
 try {
  const [first, duplicate] = await Promise.all([load({shows: ['Only tech']}), load({shows: ['Only money']})]);
  assert.equal(calls, 1);
  assert.equal(first.picks.length, 12);
  assert.deepEqual([...new Set(first.picks.map(p => p.category))], ['Science', 'Tech', 'History', 'Culture', 'Mind', 'Money']);
  for (const category of new Set(first.picks.map(p => p.category))) {
   assert.equal(first.picks.filter(p => p.category === category).length, 2);
  }
  assert.equal(new Set(first.picks.map(p => p.audioUrl)).size, 12);
  assert.ok(first.picks.every(p => p.durationMs === 1800000 && p.reason.split(/\s+/).length >= 5 && p.reason.split(/\s+/).length <= 9));
  assert.deepEqual(first, duplicate);
  assert.equal((await load({})).cached, true);
  assert.equal(calls, 1);
 } finally {fs.rmSync(dir, {recursive: true, force: true});}
});

test('failed lookups are not saved as recommendations', async () => {
 const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'podcast-picks-'));
 const load = podcastRecommendations(dir, {responses: {create: async () => {throw Error('No catalog should reach AI');}}},
  {fetch: async () => ({ok: false})});
 try {
  await assert.rejects(load({}), /No verified/);
  assert.equal(fs.readdirSync(dir).length, 0);
 } finally {fs.rmSync(dir, {recursive: true, force: true});}
});
