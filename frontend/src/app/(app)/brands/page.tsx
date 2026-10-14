"use client";

import { Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { PageHeader } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { TBody, THead, Table, Td, Th, Tr } from "@/components/ui/table";
import { useBrandMutations, useBrands } from "@/lib/api/hooks";

const splitList = (v: string) =>
  v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

export default function BrandsPage() {
  const { data } = useBrands();
  const { create, remove } = useBrandMutations();
  const hasPrimary = data?.items.some((b) => b.role === "primary") ?? false;
  const [name, setName] = useState("");
  // The first brand defaults to "primary" (your own); once one exists, only competitors.
  const [roleChoice, setRoleChoice] = useState<"primary" | "competitor" | null>(null);
  const role = hasPrimary ? "competitor" : (roleChoice ?? "primary");
  const [aliases, setAliases] = useState("");
  const [domains, setDomains] = useState("");

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name: name.trim(), role, aliases: splitList(aliases), domains: splitList(domains) },
      {
        onSuccess: () => {
          setName("");
          setAliases("");
          setDomains("");
          setRoleChoice(null);
        },
      },
    );
  }

  return (
    <>
      <PageHeader title="Brands" description="Your brand and the competitors to benchmark against." />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card>
          <CardContent className="pt-5">
            <Table>
              <THead>
                <Tr>
                  <Th>Name</Th>
                  <Th>Role</Th>
                  <Th>Aliases</Th>
                  <Th>Domains</Th>
                  <Th className="w-10" />
                </Tr>
              </THead>
              <TBody>
                {data?.items.map((b) => (
                  <Tr key={b.id}>
                    <Td className="font-medium">{b.name}</Td>
                    <Td>
                      <Badge variant={b.role === "primary" ? "info" : "default"}>{b.role}</Badge>
                    </Td>
                    <Td className="text-muted-foreground">{b.aliases.join(", ") || "—"}</Td>
                    <Td className="text-muted-foreground">{b.domains.join(", ") || "—"}</Td>
                    <Td>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${b.name}`}
                        onClick={() => {
                          if (confirm(`Delete ${b.name}? Its historical mentions are removed too.`)) remove.mutate(b.id);
                        }}
                      >
                        <Trash2 />
                      </Button>
                    </Td>
                  </Tr>
                ))}
                {data && data.items.length === 0 ? (
                  <Tr>
                    <Td colSpan={5} className="py-8 text-center text-muted-foreground">
                      Start by adding your own brand as primary.
                    </Td>
                  </Tr>
                ) : null}
              </TBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Add a brand</CardTitle>
            <CardDescription>Aliases catch alternative spellings; typos are matched fuzzily.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-3" aria-label="Add brand">
              <div className="space-y-1">
                <Label htmlFor="brand-name">Name</Label>
                <Input id="brand-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div className="space-y-1">
                <Label htmlFor="brand-role">Role</Label>
                <Select
                  id="brand-role"
                  className="w-full"
                  value={role}
                  disabled={hasPrimary}
                  onChange={(e) => setRoleChoice(e.target.value as "primary" | "competitor")}
                >
                  <option value="primary">Primary (your brand)</option>
                  <option value="competitor">Competitor</option>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="brand-aliases">Aliases (comma separated)</Label>
                <Input id="brand-aliases" value={aliases} onChange={(e) => setAliases(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="brand-domains">Domains (comma separated)</Label>
                <Input
                  id="brand-domains"
                  placeholder="example.com"
                  value={domains}
                  onChange={(e) => setDomains(e.target.value)}
                />
              </div>
              {create.error ? <p className="text-sm text-destructive">{create.error.message}</p> : null}
              <Button type="submit" disabled={create.isPending} className="w-full">
                Add brand
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
