// Run manually with a dedicated public-catalogue administrator, never the app user.
// Grant the publisher readWrite ONLY on ducktape_public; grant runtime readers read.
// Do not grant either role access to private evidence_vectors or run metrics.
const catalogue = db.getSiblingDB('ducktape_public');
catalogue.global_entries.createIndex(
  { 'affected.ecosystem': 1, 'affected.package': 1 },
  { name: 'exact_public_package' }
);
// MongoDB's unique _id indexes enforce immutable revision identities and heads.
catalogue.global_revisions.createIndex({ canonical_id: 1, material_revision: 1 }, { unique: true });
