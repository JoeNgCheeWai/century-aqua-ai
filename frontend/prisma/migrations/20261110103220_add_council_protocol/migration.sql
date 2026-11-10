-- CreateEnum
CREATE TYPE "CouncilStatus" AS ENUM ('RUNNING', 'RESOLVED', 'ESCALATED_TO_CEO', 'NEEDS_CEO_INPUT', 'FAILED');

-- CreateTable
CREATE TABLE "CouncilSessionLog" (
    "id" TEXT NOT NULL,
    "query" TEXT NOT NULL,
    "participatingAgents" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "selectionRationale" TEXT NOT NULL,
    "status" "CouncilStatus" NOT NULL DEFAULT 'RUNNING',
    "conflictMatrix" JSONB,
    "hardConstraintVetoes" JSONB,
    "cooModerationNotes" TEXT,
    "caoSynthesizedOptions" JSONB,
    "redTeamFindings" JSONB,
    "dissentRecord" JSONB,
    "ceoQuestions" JSONB,
    "escalationReason" TEXT,
    "modelCalls" INTEGER NOT NULL DEFAULT 0,
    "totalTokens" INTEGER,
    "startedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMP(3),
    "humanAction" "HumanAction" NOT NULL DEFAULT 'Pending',

    CONSTRAINT "CouncilSessionLog_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CouncilTurnLog" (
    "id" TEXT NOT NULL,
    "sessionId" TEXT NOT NULL,
    "round" INTEGER NOT NULL,
    "agentId" TEXT NOT NULL,
    "position" TEXT NOT NULL,
    "hardConstraints" JSONB,
    "confidence" INTEGER,
    "sourcesCited" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "auditLogId" INTEGER,
    "tokensUsed" INTEGER,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CouncilTurnLog_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "CouncilSessionLog_status_idx" ON "CouncilSessionLog"("status");

-- CreateIndex
CREATE INDEX "CouncilSessionLog_startedAt_idx" ON "CouncilSessionLog"("startedAt");

-- CreateIndex
CREATE INDEX "CouncilSessionLog_humanAction_idx" ON "CouncilSessionLog"("humanAction");

-- CreateIndex
CREATE INDEX "CouncilTurnLog_sessionId_round_idx" ON "CouncilTurnLog"("sessionId", "round");

-- CreateIndex
CREATE INDEX "CouncilTurnLog_agentId_timestamp_idx" ON "CouncilTurnLog"("agentId", "timestamp");

-- AddForeignKey
ALTER TABLE "CouncilTurnLog" ADD CONSTRAINT "CouncilTurnLog_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES "CouncilSessionLog"("id") ON DELETE CASCADE ON UPDATE CASCADE;
