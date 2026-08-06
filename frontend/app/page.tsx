import { Radar } from "../components/radar";
import { getRadar } from "../lib/api";

export default async function Home() {
  const rows = await getRadar();
  return <Radar initialRows={rows} />;
}
