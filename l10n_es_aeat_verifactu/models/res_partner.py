# Copyright 2024 Aures TIC - Jose Zambudio
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    aeat_simplified_invoice = fields.Boolean("AEAT Simplified Invoice")
    aeat_identification_type = fields.Selection(
        string="AEAT Identification type",
        help=(
            "Used to specify an identification type to send to AEAT. Normally for "
            "sending national and export invoices to AEAT where the customer country "
            "is not Spain, it would calculate an identification type of 04 if the VAT "
            "field is filled and 06 if it was not. This field is to specify "
            "types of 03 through 05, in the event that the customer doesn't identify "
            "with a foreign VAT and instead with their passport "
            "or residential certificate. If there is no value it will work as before."
        ),
        selection=[
            ("03", "Passport"),
            ("05", "Residential certificate"),
            ("06", "Another document"),
        ],
    )
    aeat_identification = fields.Char(help="Identification for AEAT purposes")


    #@ormcache("self.env")
    def _get_aeat_europe_codes(self):
        europe = self.env.ref("base.europe", raise_if_not_found=False)
        if not europe:
            europe = self.env["res.country.group"].search(
                [("name", "=", "Europe")], limit=1
            )
        return europe.country_ids.mapped("code")

    def _map_aeat_country_code(self, country_code, extended=False):
        """Map country codes according the fiscal conditions.

        :arg boolean extended: If True, it means you want to convert also special
          territories like Overseas France. That ones are not considered for
          intracommunity operations, but they need to use FR country code for
          identification purposes.
        :return: The mapped country code if exists, or the same country code if not.
        """
        country_code_map = {"EL": "GR"}
        if extended:
            country_code_map.update(
                {
                    "RE": "FR",
                    "GP": "FR",
                    "MQ": "FR",
                    "GF": "FR",
                    "PF": "FR",
                    "BL": "FR",
                    "MF": "FR",
                    "PM": "FR",
                    "WF": "FR",
                }
            )
        return country_code_map.get(country_code, country_code)


    #@ormcache("self.vat, self.country_id")
    def _parse_aeat_vat_info(self):
        """Return tuple with split info (country_code, identifier_type and
        vat_number) from vat and country partner
        """
        self.ensure_one()
        vat_number = self.vat or ""
        prefix = vat_number[:2].upper()
        if self._map_aeat_country_code(prefix) in self._get_aeat_europe_codes():
            country_code = prefix
            vat_number = vat_number[2:]
            identifier_type = "02"
        else:
            if self.country_id.code:
                country_code = self.country_id.code
            elif self.env["res.country"].search([("code", "=", prefix)]):
                country_code = prefix
            else:
                country_code = ""
            if (
                self._map_aeat_country_code(country_code)
                in self._get_aeat_europe_codes()
            ):
                identifier_type = "02"
            else:
                country_code = self._map_aeat_country_code(country_code, extended=True)
                identifier_type = "04"
        if country_code == "ES":
            identifier_type = ""
        return (
            country_code,
            self.aeat_identification_type or identifier_type,
            self.aeat_identification if self.aeat_identification_type else vat_number,
        )



    """@api.multi
    @api.depends("company_id")
    def _compute_aeat_sending_enabled(self):
        res = super()._compute_aeat_sending_enabled()
        verifactu_enabled = any(self.env.companies.mapped("verifactu_enabled"))
        for partner in self:
            verifactu_enabled = (
                partner.company_id.verifactu_enabled
                if partner.company_id
                else verifactu_enabled
            )
            partner.verifactu_enabled = verifactu_enabled
            if verifactu_enabled:
                partner.aeat_sending_enabled = True
        return res"""
