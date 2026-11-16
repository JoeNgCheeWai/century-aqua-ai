-- CreateTable
-- One generic table for the farm and operations records the CAO and the COO read. A single table (not six) so a new kind of record is a new
-- value of "kind", not a migration. WHICH ROLE MAY READ WHICH KIND is enforced by the backend (app/governance/farm_records.py), not by the
-- database: the CAO reads water_quality, feeding, mortality and sampling; the COO reads inventory and cost. This migration only ADDS a table.
CREATE TABLE "FarmRecord" (
    "id" SERIAL NOT NULL,
    "kind" TEXT NOT NULL,
    "site" TEXT,
    "unit" TEXT,
    "parameter" TEXT NOT NULL,
    "value" DOUBLE PRECISION,
    "valueText" TEXT,
    "measuredUnit" TEXT,
    "recordedAt" TIMESTAMP(3) NOT NULL,
    "source" TEXT,
    "notes" TEXT,
    "importBatch" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "FarmRecord_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "FarmRecord_kind_recordedAt_idx" ON "FarmRecord"("kind", "recordedAt");

-- CreateIndex
CREATE INDEX "FarmRecord_kind_parameter_idx" ON "FarmRecord"("kind", "parameter");

-- CreateIndex
CREATE INDEX "FarmRecord_importBatch_idx" ON "FarmRecord"("importBatch");
