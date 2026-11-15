-- CreateTable
CREATE TABLE "ApprovedStrategies" (
    "id" SERIAL NOT NULL,
    "agentRole" TEXT NOT NULL,
    "strategySummary" TEXT NOT NULL,
    "context" TEXT NOT NULL,
    "auditLogId" INTEGER,
    "approvedBy" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ApprovedStrategies_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "ApprovedStrategies_auditLogId_key" ON "ApprovedStrategies"("auditLogId");

-- CreateIndex
CREATE INDEX "ApprovedStrategies_agentRole_createdAt_idx" ON "ApprovedStrategies"("agentRole", "createdAt");