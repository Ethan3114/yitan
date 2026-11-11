import { Welcome } from "@/components/welcome";
export default async function WelcomePage({ searchParams }: { searchParams: Promise<{ step?: string }> }) {
  const { step } = await searchParams;
  const value = Number(step ?? 0);
  return <Welcome step={Number.isInteger(value) && value >= 0 && value <= 5 ? value : 0} />;
}
