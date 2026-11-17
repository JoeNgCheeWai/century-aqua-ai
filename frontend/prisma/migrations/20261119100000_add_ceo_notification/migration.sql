-- CreateTable
-- A notification for the CEO about a CRITICAL decision the COO escalated (the COO is the sole approver of everything else). One row per task that
-- needs the CEO: the plain-language why, the COO's recommendation, and whether it has been read or settled. This migration only ADDS one table and
-- two indexes; it changes nothing that exists.
CREATE TABLE "CeoNotification" (
    "id" SERIAL NOT NULL,
    "auditId" INTEGER,
    "agentId" TEXT,
    "title" TEXT NOT NULL,
    "explanation" TEXT NOT NULL,
    "recommendation" TEXT,
    "riskLevel" TEXT,
    "status" TEXT NOT NULL DEFAULT 'unread',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "readAt" TIMESTAMP(3),
    "resolvedAt" TIMESTAMP(3),
    "resolvedBy" TEXT,

    CONSTRAINT "CeoNotification_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "CeoNotification_status_createdAt_idx" ON "CeoNotification"("status", "createdAt");

-- CreateIndex
CREATE INDEX "CeoNotification_auditId_idx" ON "CeoNotification"("auditId");
