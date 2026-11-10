-- CreateEnum
CREATE TYPE "HumanAction" AS ENUM ('Approved', 'Modified', 'Rejected', 'Pending');

-- CreateTable
CREATE TABLE "AgentState" (
    "id" SERIAL NOT NULL,
    "agentId" TEXT NOT NULL,
    "conversationHistory" JSONB NOT NULL DEFAULT '[]',
    "resetEpoch" INTEGER NOT NULL DEFAULT 0,
    "lastReset" TIMESTAMP(3),
    "constitutionalViolations" INTEGER NOT NULL DEFAULT 0,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "AgentState_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AgentAuditLog" (
    "id" SERIAL NOT NULL,
    "agentId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "query" TEXT NOT NULL,
    "response" TEXT NOT NULL,
    "constitutionalScore" INTEGER NOT NULL,
    "sourcesCited" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "humanAction" "HumanAction" NOT NULL DEFAULT 'Pending',
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "AgentAuditLog_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CrossValidationLog" (
    "id" SERIAL NOT NULL,
    "agentAId" TEXT NOT NULL,
    "agentBId" TEXT NOT NULL,
    "query" TEXT NOT NULL,
    "response" JSONB NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CrossValidationLog_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "AgentState_agentId_key" ON "AgentState"("agentId");

-- CreateIndex
CREATE INDEX "AgentAuditLog_agentId_timestamp_idx" ON "AgentAuditLog"("agentId", "timestamp");

-- CreateIndex
CREATE INDEX "AgentAuditLog_userId_timestamp_idx" ON "AgentAuditLog"("userId", "timestamp");

-- CreateIndex
CREATE INDEX "AgentAuditLog_humanAction_idx" ON "AgentAuditLog"("humanAction");

-- CreateIndex
CREATE INDEX "CrossValidationLog_agentAId_agentBId_timestamp_idx" ON "CrossValidationLog"("agentAId", "agentBId", "timestamp");
