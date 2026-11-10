-- AlterTable
ALTER TABLE "AgentAuditLog" ADD COLUMN     "preConsultationRequests" JSONB,
ADD COLUMN     "reasoningChain" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN     "scenarios" JSONB;
