import ComingSoon from "@/components/staff/ComingSoon";

export default function AdminUsersPage() {
  return (
    <ComingSoon
      title="Users"
      phase="Phase 3"
      description="Manage accounts and plans. Until this page exists, change a user's role by hand in Prisma Studio (npx prisma studio)."
      planned={[
        "Searchable, filterable user table with role, message count and sign-up date",
        "Change a role, reset a message count, extend a session window, ban a user",
        "Bulk role changes, with every admin action written to the audit log",
      ]}
    />
  );
}
