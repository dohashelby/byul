These are unchanged Git blob bytes from commit
`05035675d73b8379553b3d7845c46d5f43ec9243` under
`update/packs/PACK-BASELINE`. They restore the published pack38 over a current
pack copy; `load_pack` verifies every other inherited file against this original
manifest. The original canonical pack Ref hash is
`22dee30a0df2e9e79defd6d28010f3d16e202512d67269e831d3c5fa95113d80`.

- pack.yaml bytes SHA256: `3dc0151cc370cc35690f77ee34a4bba64b526537cd6c6a8025d45d6b492f907e`
- checks/REQ-08.yaml bytes SHA256: `066c20539781dfd7c4ff5f94ef87baf91e58f8088f9fbcab974dda5bf62c7cfa`
- checks/REQ-09.yaml bytes SHA256: `e64afccd3484b495a607eb603f3acdf340d6cb53c7a3b15de4bbd1553e95cfc3`

The same two unchanged check blobs also complete the older pack37 overlay.
Their hashes match its existing manifest. No historical definition or manifest
is regenerated for the newer input profiles.
