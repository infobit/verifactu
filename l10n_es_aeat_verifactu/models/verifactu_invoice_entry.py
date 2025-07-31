# -*- coding: utf-8 -*-
# Copyright 2025 ForgeFlow S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import datetime
import json
import logging

from requests import Session

from openerp import _, api, fields, models
from openerp.exceptions import except_orm, Warning, RedirectWarning, ValidationError
from datetime import datetime, timedelta
_logger = logging.getLogger(__name__)
from lxml import etree


try:
    from zeep import Client, Settings
    from zeep.plugins import HistoryPlugin
    from zeep.transports import Transport
except (ImportError, IOError) as err:
    _logger.debug(err)


VERIFACTU_SEND_STATES = [
    ("not_sent", "Not sent"),
    ("correct", "Sent and Correct"),
    ("incorrect", "Sent and Incorrect"),
    ("accepted_with_errors", "Sent and accepted with errors"),
]

VERIFACTU_STATE_MAPPING = {
    "Correcto": "correct",
    "Incorrecto": "incorrect",
    "AceptadoConErrores": "accepted_with_errors",
}


class VerifactuInvoiceEntry(models.Model):
    _name = "verifactu.invoice.entry"
    _description = "VeriFactu Invoice Entry"
    _order = "id desc"
    _rec_name = "document_hash"

    """verifactu_chaining_id = fields.Many2one(
        "verifactu.chaining",
        string="Chaining",
        ondelete="restrict",
    )"""
    model = fields.Char(readonly=True)
    document_id = fields.Many2one(
        "account.invoice",
        string="Document",
        readonly=True,
        index=True,
    )
    document_name = fields.Char(readonly=True)
    previous_invoice_entry_id = fields.Many2one(
        "verifactu.invoice.entry",
        string="Previous Invoice Entry",
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        readonly=True,
    )
    document_hash = fields.Char(
        required=True,
        readonly=True,
    )
    aeat_json_data = fields.Text(
        string="AEAT JSON Data",
        help="Generated JSON data to send to AEAT",
        readonly=True,
    )
    send_state = fields.Selection(
        selection=VERIFACTU_SEND_STATES,
        string="Verifactu send state",
        compute="_compute_send_state",
        default="not_sent",
        readonly=True,
        store=True,
        copy=False,
        help="Indicates the state of this document in relation with the "
        "presentation to Verifactu.",
    )
    send_attempt = fields.Integer(
        default=0, help="Number of attempts to send this document."
    )
    company_id = fields.Many2one("res.company", required=True)
    response_line_ids = fields.One2many(
        "verifactu.invoice.entry.response.line",
        "entry_id",
        string="Responses",
        help="Responses from Verifactu after sending the documents.",
    )
    last_error_code = fields.Char(compute="_compute_last_error_code", store=True)
    previous_hash = fields.Char(
        related="previous_invoice_entry_id.document_hash",
        readonly=True,
        string="Previous Hash",
    )
    entry_type = fields.Selection(
        selection=[
            ("register", "Register"),
            ("modify", "Modify"),
            ("cancel", "Cancel"),
        ],
        default="register",
        required=True,
    )
    last_response_line_id = fields.Many2one(
        "verifactu.invoice.entry.response.line",
        string="Last Response Line",
        readonly=True,
    )

    @api.depends("last_response_line_id.send_state", "response_line_ids", "response_line_ids.send_state")
    def _compute_send_state(self):
        for rec in self:
            rec.send_state = "not_sent"
            last_response = rec.last_response_line_id
            if last_response:
                rec.send_state = last_response.send_state

    @api.depends("response_line_ids", "response_line_ids.error_code")
    def _compute_last_error_code(self):
        """Compute the last error code from the response lines."""
        for rec in self:
            if rec.last_response_line_id:
                rec.last_error_code = rec.last_response_line_id.error_code
            else:
                rec.last_error_code = ""

    @property
    def document(self):
        return self.env[self.model].browse(self.document_id).exists()

    @api.model
    def _cron_send_documents_to_verifactu(self):
        for company in self.env["res.company"].search(
            [("verifactu_enabled", "=", True)]
        ):
            # Look for documents where we have to send as an incident
            self.env.cr.execute(
                """
                SELECT id FROM verifactu_invoice_entry AS vsq
                WHERE vsq.send_state in ('not_sent', 'incorrect')
                AND vsq.company_id = %s
                ORDER BY id
                FOR UPDATE NOWAIT
                """,
                [company.id],  # Always use a list or tuple here
            )
            records_to_send = self.browse(r[0] for r in self.env.cr.fetchall())
            send_date = datetime.now() 
            threshold_time = send_date - timedelta(seconds=240)
            outdated_records = records_to_send.filtered(
                lambda r: datetime.strptime(r.document_id.verifactu_registration_date, '%Y-%m-%d %H:%M:%S') < threshold_time
            )
            current_records = records_to_send - outdated_records
            outdated_records.with_context(
                verifactu_incident=True
            )._send_documents_to_verifactu()
            current_records._send_documents_to_verifactu()
        return True

    def _get_verifactu_aeat_header(self):
        """Builds VERIFACTU send header

        :param tipo_comunicacion String 'A0': new reg, 'A1': modification
        :param cancellation Bool True when the communitacion es for document
            cancellation
        :return Dict with header data depending on cancellation
        """
        # todo: implementar RemisionVoluntaria
        self.ensure_one()
        if not self.company_id.vat:
            raise ValidationError(
                _("No VAT configured for the company '{}'").format(self.company_id.name)
            )
        header = {
            "ObligadoEmision": {
                "NombreRazon": self.company_id.name[0:120],
                "NIF": self.company_id.partner_id._parse_aeat_vat_info()[2],
            },
        }
        incident = self.env.context.get("verifactu_incident", False)
        if incident:
            header.update({"RemisionVoluntaria": {"Incidencia": "S"}})
        return header

    def _connect_params_aeat(self, mapping_key):
        self.ensure_one()
        agency = self.company_id.tax_agency_id
        if not agency:
            # We use spanish agency by default to keep old behavior with
            # ir.config parameters. In the future it might be good to reinforce
            # to explicitly set a tax agency in the company by raising an error
            # here.
            agency = self.env.ref("l10n_es_aeat_verifactu.aeat_tax_agency_spain")
        return agency._connect_params_verifactu(mapping_key, self.company_id)

    def _get_mapping_key(self):
        return self.type

    def _connect_verifactu(self, mapping_key):
        # de momento no puedo el _connect_aeat del aeat_mixin porque si no pongo
        # forbid_entities en settings del Client da error de entities forbiden
        self.ensure_one()
        public_crt = self.env['ir.config_parameter'].sudo().get_param(
            'l10n_es_aeat_verifactu.publicCrt', False)
        private_key = self.env['ir.config_parameter'].sudo().get_param(
            'l10n_es_aeat_verifactu.privateKey', False)
        params = self._connect_params_aeat(mapping_key)
        parser = etree.XMLParser(resolve_entities=False)
        session = Session()
        session.cert = (public_crt, private_key)
        transport = Transport(session=session) #, xml_headers={'Content-Type': 'application/soap+xml'})
        history = HistoryPlugin()
        settings = Settings(forbid_entities=False)
        wsdl=params["wsdl"]
        client = Client(
            wsdl=params["wsdl"],
            transport=transport,
            plugins=[history],
            settings=settings,
        )
        return self._bind_service(client, params["port_name"], params["address"])

    def _bind_service(self, client, port_name, address=None):
        self.ensure_one()
        service = client._get_service("sfVerifactu")
        port = client._get_port(service, port_name)
        address = address or port.binding_options["address"]
        return client.create_service(port.binding.name, address)

    def _process_response_line_doc_vals(
        self,
        verifactu_response=False,
        verifactu_response_line=False,
        response_line=False,
        previous_response_line=False,
        header_sent=False,
    ):
        estado_registro = verifactu_response_line["EstadoRegistro"]
        doc_vals = {
            "verifactu_header_sent": json.dumps(header_sent, indent=4),
        }
        doc_vals["verifactu_return"] = verifactu_response_line
        send_error = False
        if hasattr(verifactu_response_line, "CodigoErrorRegistro"):
            send_error = "{} | {}".format(
                str(verifactu_response_line["CodigoErrorRegistro"]),
                #verifactu_response_line["DescripcionErrorRegistro"].encode('utf-8'),
                str(verifactu_response_line["DescripcionErrorRegistro"]),
            )
            # si ya ha devuelto previamente registro duplicado, parseamos el estado
            # del registro duplicado para dejar la factura correcta o incorrecta
            if (
                verifactu_response_line["CodigoErrorRegistro"] == 3000
                and previous_response_line
                and (
                    previous_response_line.error_code == "3000"
                    and previous_response_line.send_state == "incorrect"
                )
            ):
                registroDuplicado = verifactu_response_line["RegistroDuplicado"]
                estado_registro = registroDuplicado["EstadoRegistroDuplicado"]
                # en duplicados devuelve Correcta en vez de Correcto...
                if estado_registro == "Correcta":
                    estado_registro = "Correcto"
                    response_line.send_state = "correct"
                    response_line.entry_id.send_state = "correct"
                elif registroDuplicado["CodigoErrorRegistro"]:
                    # en duplicados devuelve AceptadaConErrores en vez de AceptadoConErrores...
                    if estado_registro == "AceptadaConErrores":
                        estado_registro = "AceptadoConErrores"
                        response_line.send_state = "accepted_with_errors"
                        response_line.entry_id.send_state = "accepted_with_errors"
                    send_error = "{} | {}".format(
                        str(registroDuplicado["CodigoErrorRegistro"]),
                        str(registroDuplicado["DescripcionErrorRegistro"]), #.encode('utf-8'),
                    )
        if estado_registro == "Correcto":
            doc_vals.update(
                {
                    "verifactu_state": "sent",
                    "verifactu_csv": verifactu_response["CSV"],
                    "verifactu_send_failed": False,
                }
            )
            
        elif estado_registro == "AceptadoConErrores":
            doc_vals.update(
                {
                    "verifactu_state": "sent_w_errors",
                    "verifactu_csv": verifactu_response["CSV"],
                    "verifactu_send_failed": True,
                }
            )
            response_line.entry_id.send_state = "accepted_with_errors"
        else:
            doc_vals["verifactu_send_failed"] = True
            response_line.entry_id.send_state = "incorrect"
        doc_vals["verifactu_send_error"] = send_error
        if response_line.document_id:
            response_line.document_id.write(doc_vals)
        return doc_vals


    def _send_documents_to_verifactu(self):
        if not self:
            return False
        rec = self[0]
        header = rec._get_verifactu_aeat_header()
        registro_factura_list = []
        create_exception = False
        for rec in self:
            rec.send_attempt += 1
            if rec.document_id:
                inv_dict = rec.document_id._get_verifactu_invoice_dict()
                registro_factura_list.append(inv_dict)
        try:
            mapping_key = rec.document_id._get_mapping_key()
            serv = rec._connect_verifactu(mapping_key)
            res = serv.RegFactuSistemaFacturacion(header, registro_factura_list)
        except Exception as fault:
            res = _("Error when trying to connect to Veri*FACTU: {}") #.format(e)
            raise ValidationError(fault)
            create_exception = True
        response_name = ""
        response = (
            self.env["verifactu.invoice.entry.response"]
            .sudo()
            .create(
                {
                    "header": json.dumps(header),
                    "name": response_name,
                    "invoice_data": json.dumps(registro_factura_list),
                    "response": res,
                    "verifactu_csv": "CSV" in res and res["CSV"] or _("-"),
                }
            )
        )
        response.complete_open_activity_on_exception()
        if create_exception:
            if not response.datetime:
                response.datetime = fields.Datetime.now()
            response.create_activity_on_exception()
        else:
            response.complete_open_activity_on_exception()
        create_response_activity = self._create_response_lines(
            response=response, header=header, verifactu_response=res
        )
        updated_response_name = _("Verifactu sending")
        if create_exception:
            updated_response_name = _("Connection error with Verifactu")
        elif create_response_activity:
            updated_response_name = _("Incorrect invoices sent to Verifactu")
        response.name = updated_response_name

        return True

    def _create_response_lines(
        self, response=False, header=False, verifactu_response=False
    ):
        create_response_activity = False
        respuestaLineas = (
            "RespuestaLinea" in verifactu_response
            and verifactu_response["RespuestaLinea"]
            or []
        )
        document_models = self.env[
            "account.invoice"
        ]._selection_verifactu_reference_models()
        for verifactu_response_line in respuestaLineas:
            invoice_num = verifactu_response_line["IDFactura"]["NumSerieFactura"]
            for model in document_models: #models[0]
                document = self.env[model[0]].search(
                    [
                        ("number", "=", invoice_num),
                        ("id", "in", self.mapped("document_id").ids),
                    ],
                    limit = 1,
                )
                if document:
                   break
            # Find the verifactu.invoice entry for this document
            verifactu_invoice_entry = document.last_verifactu_invoice_entry_id
            previous_response_line = document.last_verifactu_response_line_id
            estado_registro = verifactu_response_line["EstadoRegistro"]
            vals = {
                "entry_id": verifactu_invoice_entry.id,
                "model": verifactu_invoice_entry.model,
                "document_id": verifactu_invoice_entry.document_id.id,
                "response": verifactu_response_line,
                "entry_response_id": response.id,
                "send_state": VERIFACTU_STATE_MAPPING[estado_registro],
                "error_code": "CodigoErrorRegistro" in verifactu_response_line
                and str(verifactu_response_line["CodigoErrorRegistro"])
                or "",
            }
            response_line = (
                self.env["verifactu.invoice.entry.response.line"].sudo().create(vals)
            )
            document.last_verifactu_response_line_id = response_line
            verifactu_invoice_entry.last_response_line_id = response_line
            self._process_response_line_doc_vals(
                verifactu_response=verifactu_response,
                verifactu_response_line=verifactu_response_line,
                response_line=response_line,
                previous_response_line=previous_response_line,
                header_sent=header,
            )
            send_state = VERIFACTU_STATE_MAPPING.get(
                verifactu_response_line["EstadoRegistro"], ""
            )
            if send_state != "correct":
               create_response_activity = True
        return create_response_activity
